"""The C++ execution lane: classify a roadmap request, bound it, and build it with the local C++ builder.

The Python product lane cannot build a repository without a Python package (Everward). This module is the deterministic
front of the native lane; the model is only the author and every decision here is plain code:

* ``classify`` accepts a request only for a repository with a registered ``CppProfile`` and a ``cpp`` section on the
  request, so an ordinary or unknown request is never silently treated as native work.
* ``scope_problems`` rejects any request whose allowed paths leave the profile's roots (simulation headers and their
  tests), whose forbidden paths are missing the profile's protected set, or that names files outside its own slice.
* ``make_task`` turns the request into an ``iron_god_cpp_builder.Task`` with the profile's real CMake/MSVC commands, and
  ``author`` runs the generate -> compile -> test -> repair loop through the shared guarded Ollama door
  (``iron_god_local_model.guarded_generate``: one model slot, 3B by default, memory admission, cancellation, unload).
* ``evidence_record`` is what the lane stores: the final diff, every iteration's diagnostics, the model used, lowest
  available memory and the oracle result. Nothing here pushes or opens a pull request; delivery stays with the lane's
  existing guarded GitHub path and policy checks.

A standalone C++ test is *not* proof that Unreal works: ``unreal_required`` marks requests for engine modules, which this
lane refuses until an Unreal Build Tool profile exists and its toolchain is verified (``toolchain_problems``).
"""

from __future__ import annotations

import posixpath
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import iron_god_cpp_builder as cb

CPP_BUILDER_MODEL = "qwen2.5-coder:3b"
IDENT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class CppProfile:
    """Everything the lane knows about one native repository; a request can never widen it."""

    repository: str
    cmake_file: str                      # the CMake file that registers test executables
    link_target: str                     # library every test links against
    source_roots: tuple[str, ...]        # directories a slice may create files in
    protected: tuple[str, ...]           # paths a slice may never touch (a request must forbid them explicitly)
    configure: tuple[str, ...]           # argv of the configure step; {source} and {build} are substituted
    generator: str = "Visual Studio 18 2026"
    build_tool_args: tuple[str, ...] = ("/nodeReuse:false", "/m:2", "/v:minimal")
    unreal: bool = False


EVERWARD = CppProfile(
    repository="jweter/Project-Everward",
    cmake_file="src/simulation/CMakeLists.txt",
    link_target="everward_simulation",
    source_roots=("src/simulation/include/", "src/simulation/tests/"),
    protected=("src/simulation/CMakeLists.txt", ".github/", "engineering/", "docs/", "THIRD_PARTY_NOTICES.md", "LICENSE"),
    configure=("cmake", "-S", "{source}", "-B", "{build}", "-G", "{generator}", "-A", "x64", "-DBUILD_TESTING=ON"),
)
PROFILES: dict[str, CppProfile] = {EVERWARD.repository: EVERWARD}


def profile_for(repository: str | None) -> CppProfile | None:
    return PROFILES.get(str(repository or ""))


def target_name(request_id: str) -> str:
    """A deterministic CMake target for a slice, so the same request always registers the same test."""
    return "ew_" + IDENT.sub("_", request_id.lower()).strip("_")[:48] + "_tests"


def classify(request: dict[str, Any]) -> CppProfile | None:
    """The profile that may build this request, or None. Requires a registered repository AND an explicit ``cpp`` section."""
    profile = profile_for(request.get("repository"))
    cpp = request.get("cpp")
    if profile is None or not isinstance(cpp, dict):
        return None
    return profile


def _under(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root.rstrip("/") or path.startswith(root) for root in roots)


def scope_problems(request: dict[str, Any], profile: CppProfile) -> list[str]:
    """Reasons the request must not run. Empty means the lane may build it. Checked before any model call."""
    problems: list[str] = []
    cpp = request.get("cpp") or {}
    if profile.unreal or cpp.get("unreal_required"):
        problems.append("UNREAL_PROFILE_NOT_AVAILABLE")
    header, test = str(cpp.get("header") or ""), str(cpp.get("test") or "")
    if not header or not test:
        problems.append("CPP_FILES_NOT_DECLARED")
    for label, path in (("header", header), ("test", test)):
        norm = path.replace("\\", "/")
        if not path:
            continue
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in norm.split("/") or posixpath.normpath(norm) != norm:
            problems.append(f"PATH_NOT_CANONICAL:{label}")
        elif not _under(norm, profile.source_roots):
            problems.append(f"OUT_OF_PROFILE_ROOTS:{label}")
        elif _under(norm, profile.protected):
            problems.append(f"PROTECTED_PATH:{label}")
    allowed = [str(item).replace("\\", "/") for item in request.get("allowed_paths") or []]
    if not allowed:
        problems.append("NO_ALLOWED_PATHS")
    for pattern in allowed:
        if not _under(pattern.split("*", 1)[0], profile.source_roots) or ".." in pattern.split("/"):
            problems.append(f"ALLOWED_PATH_OUTSIDE_PROFILE:{pattern}")
    forbidden = {str(item).replace("\\", "/") for item in request.get("forbidden_paths") or []}
    for protected in profile.protected:
        if protected not in forbidden and protected.rstrip("/") + "/**" not in forbidden and protected.rstrip("/") + "/" not in forbidden:
            problems.append(f"PROTECTED_PATH_NOT_FORBIDDEN:{protected}")
    if not request.get("slice_acceptance"):
        problems.append("NO_ACCEPTANCE_CRITERIA")
    return problems


def toolchain_problems(profile: CppProfile, which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """The native toolchain must exist before any model call; Unreal needs its own verified profile on top."""
    return [f"TOOL_MISSING:{tool}" for tool in ("cmake", "ctest", "git") if which(tool) is None]


def register_cmake(workspace: Path, profile: CppProfile, name: str, source: str) -> None:
    """Deterministic scaffolding: register one test executable inside the BUILD_TESTING block of the profile's CMake file."""
    path = workspace / profile.cmake_file
    text = path.read_text(encoding="utf-8")
    if f"add_executable({name} " in text:
        return
    block = (f"    add_executable({name} {source})\n    target_link_libraries({name} PRIVATE {profile.link_target})\n"
             f"    add_test(NAME {name} COMMAND {name})\n")
    marker = text.rindex("endif()")
    path.write_text(text[:marker] + block + text[marker:], encoding="utf-8", newline="\n")


def standing_rules(cpp: dict[str, Any], profile: CppProfile) -> list[str]:
    """Compile-time facts a small model gets wrong when left to guess; derived from the request, never from the model."""
    header = str(cpp.get("header", "")).replace("\\", "/")
    root = next((r for r in profile.source_roots if r.rstrip("/").endswith("include") and header.startswith(r)), "")
    rules = []
    if root:
        include = header[len(root):]
        rules.append(f'The test file must start its includes with exactly: #include "{include}" (this is how the header is found; without it nothing compiles).')
    rules.append("The header must itself #include every standard header it uses: <cmath> for std::isnan, <algorithm> for std::clamp, std::min and std::max.")
    rules.append("There is no NaN constant: write std::numeric_limits<double>::quiet_NaN() and #include <limits> in the test.")
    inc = f'#include "{header[len(root):]}"' if root else "// include the header under test"
    skeleton = "\n".join(["#undef NDEBUG", "#include <cassert>", "#include <cmath>", "#include <limits>", inc, "",
                          "int main() {", "    assert(/* first acceptance line */);",
                          "    /* ...one assert per remaining acceptance line... */", "    return 0;", "}"])
    rules.append("The test file must be EXACTLY this skeleton with only the assert lines filled in (one assert per acceptance "
                 "line, nothing else, no namespace, no helper functions, no markdown fences):\n" + skeleton)
    return rules


def make_task(request: dict[str, Any], profile: CppProfile, workspace: Path, build_dir: Path) -> tuple[cb.Task, str]:
    """The builder task for a validated request, with the profile's real configure/build/test commands."""
    cpp = request["cpp"]
    name = target_name(str(request["id"]))
    cmake_dir = (workspace / profile.cmake_file).parent
    source = Path(cpp["test"]).relative_to(Path(profile.cmake_file).parent).as_posix()
    register_cmake(workspace, profile, name, source)
    configure = [part.format(source=str(cmake_dir), build=str(build_dir), generator=profile.generator)
                 for part in profile.configure]
    criteria = "; ".join(str(item) for item in request.get("slice_acceptance", []))
    task = cb.Task(
        id=str(request["id"]),
        objective=f"{request.get('objective', '')} Acceptance: {criteria}".strip(),
        workspace=workspace,
        allowed_paths=[str(p) for p in request["allowed_paths"]],
        forbidden_paths=[str(p) for p in request.get("forbidden_paths", [])],
        files=[cpp["header"], cpp["test"]],
        context_files=[str(p) for p in cpp.get("context", [])],
        rules=[str(r) for r in cpp.get("rules", [])] + standing_rules(cpp, profile),
        configure=[configure],
        build=["cmake", "--build", str(build_dir), "--config", "Debug", "--target", name, "--", *profile.build_tool_args],
        test=["ctest", "--test-dir", str(build_dir), "-C", "Debug", "-R", f"^{name}$", "--output-on-failure"])
    return task, name


def evidence_record(request: dict[str, Any], result: dict[str, Any], *, model: str, stats: list[dict[str, Any]],
                    lowest_available_gib: float | None) -> dict[str, Any]:
    """What the lane stores about a native run: enough to audit it without trusting the model."""
    return {"schema_version": 1, "request": request.get("id"), "repository": request.get("repository"), "model": model,
            "status": result.get("status"), "reason": result.get("reason", ""), "files": result.get("files", []),
            "iterations": result.get("iterations", []), "diff": result.get("diff", ""),
            "rolled_back": bool(result.get("rolled_back")), "model_calls": stats,
            "lowest_available_gib": lowest_available_gib, "seconds": result.get("seconds")}


@dataclass
class AuthorOutcome:
    evidence: dict[str, Any]
    ok: bool
    blocked: str = ""
    notes: list[str] = field(default_factory=list)


def author(request: dict[str, Any], workspace: Path, build_dir: Path, *, generate: Callable[..., dict[str, Any]],
           run: cb.Run = cb.default_run, which: Callable[[str], str | None] = shutil.which,
           limits: cb.Limits | None = None, requested_model: str | None = None) -> AuthorOutcome:
    """Build one validated C++ request. ``generate`` is the shared guarded door (``guarded_generate``) or a test double.

    A request outside its profile, or a missing toolchain, returns BLOCKED without a model call. A memory refusal from the
    door (``ModelNotAdmitted``) propagates so the caller can wait without spending an attempt.
    """
    profile = classify(request)
    if profile is None:
        return AuthorOutcome({}, False, "NOT_A_CPP_REQUEST")
    problems = scope_problems(request, profile) + toolchain_problems(profile, which)
    if problems:
        return AuthorOutcome({"request": request.get("id"), "status": "BLOCKED", "reason": ";".join(problems)}, False,
                             ";".join(problems))
    task, _name = make_task(request, profile, workspace, build_dir)
    stats: list[dict[str, Any]] = []
    lowest: list[float] = []
    model_used: list[str] = []

    def ask(prompt: str) -> str:
        reply = generate(prompt, requested=requested_model or CPP_BUILDER_MODEL, num_ctx=6144, num_predict=2200,
                         timeout=900)
        stats.append({key: reply.get(key) for key in ("eval_count", "eval_duration", "prompt_eval_count",
                                                      "prompt_eval_duration", "load_duration", "total_duration")})
        if reply.get("lowest_available_gib") is not None:
            lowest.append(float(reply["lowest_available_gib"]))
        model_used.append(str(reply.get("model_used") or requested_model or CPP_BUILDER_MODEL))
        return str(reply.get("response", ""))

    result = cb.build_change(task, ask, run=run, limits=limits or cb.Limits(max_iterations=4))
    record = evidence_record(request, result, model=model_used[-1] if model_used else CPP_BUILDER_MODEL, stats=stats,
                             lowest_available_gib=min(lowest) if lowest else None)
    return AuthorOutcome(record, result.get("status") == cb.SUCCESS)


# --- job bookkeeping: one native build per request, bounded attempts, a memory wait spends nothing --------------------------
MAX_ATTEMPTS = 3
WAITING_EXIT = 3


def jobs_dir(lane_root: Path) -> Path:
    return Path(lane_root) / "cpp-jobs"


def _job_file(lane_root: Path, ident: str) -> Path:
    return jobs_dir(lane_root) / (IDENT.sub("_", ident.lower()).strip("_")[:64] + ".json")


def read_job(lane_root: Path, ident: str) -> dict[str, Any]:
    import json

    try:
        return dict(json.loads(_job_file(lane_root, ident).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {"id": ident, "attempts": 0, "state": "NEW"}


def write_job(lane_root: Path, job: dict[str, Any]) -> None:
    import json

    path = _job_file(lane_root, str(job["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def advance(lane_root: Path, requests: list[dict[str, Any]], *, may_start: bool,
            spawn: Callable[[Path, Path], int | None], alive: Callable[[int], bool]) -> list[dict[str, Any]]:
    """Harvest finished native jobs and start at most one new one. Returns outcome rows for the lane log.

    ``spawn(request_file, result_file)`` launches the release-bound author and returns its pid. A job whose author exited
    with the memory-wait code is retried later without spending an attempt; any other failure spends one of MAX_ATTEMPTS.
    """
    import json

    outcomes: list[dict[str, Any]] = []
    native = [r for r in requests if classify(r) is not None]
    running = False
    for request in native:
        job = read_job(lane_root, str(request["id"]))
        if job.get("state") == "RUNNING":
            result = Path(job["result_file"])
            if result.is_file():
                data = json.loads(result.read_text(encoding="utf-8"))
                status = data.get("status")
                if status == "WAITING_FOR_MEMORY":
                    job.update(state="WAITING")
                elif status == "BUILT_AND_VERIFIED":
                    job.update(state="BUILT", result=data)
                else:
                    job.update(state="FAILED" if job["attempts"] >= MAX_ATTEMPTS else "RETRY", last=data)
                write_job(lane_root, job)
                outcomes.append({"id": job["id"], "status": status, "reason": data.get("reason", "")})
            elif alive(int(job.get("pid") or 0)):
                running = True
            else:
                job.update(state="FAILED" if job["attempts"] >= MAX_ATTEMPTS else "RETRY",
                           last={"status": "AUTHOR_EXITED_WITHOUT_RESULT"})
                write_job(lane_root, job)
                outcomes.append({"id": job["id"], "status": "AUTHOR_EXITED_WITHOUT_RESULT", "reason": ""})
    if running or not may_start:
        return outcomes
    for request in native:
        job = read_job(lane_root, str(request["id"]))
        if job.get("state") in ("NEW", "WAITING", "RETRY") and job.get("attempts", 0) < MAX_ATTEMPTS:
            request_file = jobs_dir(lane_root) / f"{_job_file(lane_root, str(request['id'])).stem}.request.json"
            result_file = jobs_dir(lane_root) / f"{_job_file(lane_root, str(request['id'])).stem}.result.json"
            result_file.unlink(missing_ok=True)
            request_file.parent.mkdir(parents=True, exist_ok=True)
            request_file.write_text(json.dumps(request, indent=2), encoding="utf-8")
            pid = spawn(request_file, result_file)
            if pid is None:
                outcomes.append({"id": job["id"], "status": "SPAWN_FAILED", "reason": ""})
                continue
            spent = 0 if job.get("state") == "WAITING" else 1  # a memory wait costs no attempt
            job.update(state="RUNNING", pid=pid, result_file=str(result_file), attempts=job.get("attempts", 0) + spent)
            write_job(lane_root, job)
            outcomes.append({"id": job["id"], "status": "STARTED", "reason": f"attempt {job['attempts']}"})
            break
    return outcomes


# --- independent verification and hand-off to the lane's publication path ---------------------------------------------------
def changed_paths(workspace: Path, base: str, head: str, run: cb.Run = cb.default_run) -> list[str] | None:
    code, out = run(["git", "diff", "--name-only", f"{base}..{head}"], workspace, 120)
    return sorted(line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()) if code == 0 else None


def change_problems(paths: list[str] | None, request: dict[str, Any], profile: CppProfile) -> list[str]:
    """The committed change may touch only the slice's declared files plus the one CMake registration."""
    if not paths:
        return ["NO_CHANGE_OR_DIFF_UNAVAILABLE"]
    allowed = {str(p).replace("\\", "/") for p in request.get("allowed_paths") or []} | {profile.cmake_file}
    return [f"OUT_OF_SCOPE_CHANGE:{p}" for p in paths if p not in allowed]


def independent_verification(request: dict[str, Any], result: dict[str, Any], build_dir: Path, *,
                             run: cb.Run = cb.default_run) -> dict[str, Any]:
    """Re-check the author's commit from the supervisor side: exact head, scope of the diff, fresh configure/build/test.

    The author's own success is never accepted as evidence; this is what the merge gate's ``_verified`` reads.
    """
    profile = classify(request)
    head, base, branch = str(result.get("head") or ""), str(result.get("base") or ""), str(result.get("branch") or "")
    report: dict[str, Any] = {"passed": False, "head_sha": head, "base_sha": base, "branch": branch, "checks": []}
    if profile is None or not head or not base:
        report["reason"] = "MISSING_HEAD_BASE_OR_PROFILE"
        return report
    workspace = Path(str(result.get("workspace") or ""))
    if not (workspace / ".git").exists():
        report["reason"] = "AUTHOR_WORKSPACE_MISSING"
        return report
    code, current = run(["git", "rev-parse", "HEAD"], workspace, 60)
    if code != 0 or current.strip() != head:
        report["reason"] = "HEAD_MISMATCH"
        return report
    problems = change_problems(changed_paths(workspace, base, head, run), request, profile)
    report["checks"].append({"name": "scope", "ok": not problems, "detail": problems})
    if problems:
        report["reason"] = ";".join(problems)
        return report
    task, name = make_task(request, profile, workspace, build_dir)  # re-registration is idempotent on a committed file
    for label, command in (("configure", task.configure[0]), ("build", task.build), ("test", task.test)):
        status, out = run(list(command), workspace, 1800)
        report["checks"].append({"name": label, "ok": status == 0, "tail": out[-400:]})
        if status != 0:
            report["reason"] = f"{label.upper()}_FAILED"
            return report
    dirty_code, dirty = run(["git", "status", "--porcelain"], workspace, 60)
    report["checks"].append({"name": "clean_tree", "ok": dirty_code == 0 and not dirty.strip()})
    report["passed"] = all(c["ok"] for c in report["checks"])
    report["target"] = name
    return report


def publication_item(request: dict[str, Any], result: dict[str, Any], verifier: str) -> dict[str, Any]:
    """The lane publication record (state PUBLISH_PENDING) for a verified native commit."""
    objective = str(request.get("objective") or request["id"])
    return {"repository": request["repository"], "branch": result["branch"], "head_sha": result["head"],
            "state": "PUBLISH_PENDING", "verified_by": verifier, "note": "native C++ lane",
            "title": f"Iron God [{request['id']}]: {objective}"[:120],
            "body": (f"Native C++ slice {request['id']} ({request.get('roadmap_ref', '')}).\n\n{objective}\n\n"
                     "Acceptance: " + "; ".join(str(a) for a in request.get("slice_acceptance", [])) + "\n\n"
                     f"Base: {result.get('base')}\nHead: {result['head']}\n"
                     "Authored by the local model through the shared guarded Ollama door; compiled and tested with the "
                     "repository's own CMake/MSVC toolchain, then re-verified from scratch by the supervisor "
                     "(scope of the diff, fresh configure/build/ctest). The pull request merges only after every CI "
                     "check passes and the merge-readiness gate agrees.")}


def finalize(lane_root: Path, requests: list[dict[str, Any]], *, save_report: Callable[[str, dict[str, Any]], None],
             add_publication: Callable[[str, dict[str, Any]], None], run: cb.Run = cb.default_run) -> list[dict[str, Any]]:
    """Verify each BUILT job once and hand it to the publication path; a failed verification spends an attempt."""
    outcomes: list[dict[str, Any]] = []
    for request in requests:
        if classify(request) is None:
            continue
        job = read_job(lane_root, str(request["id"]))
        if job.get("state") != "BUILT":
            continue
        result = dict(job.get("result") or {})
        verifier = "cpp-" + _job_file(lane_root, str(request["id"])).stem
        workspace = Path(str(result.get("workspace") or "x"))
        report = independent_verification(request, result, workspace.parent / (workspace.name + "-verify"), run=run)
        save_report(verifier, report)
        if report["passed"]:
            add_publication(verifier, publication_item(request, result, verifier))
            job.update(state="PUBLISHED", verifier=verifier)
            outcomes.append({"id": job["id"], "status": "PUBLICATION_REGISTERED", "reason": report["head_sha"][:12]})
        else:
            job.update(state="FAILED" if job.get("attempts", 0) >= MAX_ATTEMPTS else "RETRY", last=report)
            outcomes.append({"id": job["id"], "status": "VERIFICATION_FAILED", "reason": str(report.get("reason", ""))})
        write_job(lane_root, job)
    return outcomes
