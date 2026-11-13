"""Roadmap-directed refill of the strict product backlog (issue #594).

The owner's roadmap is standing development authorization, but the lane dispatches only
well-formed ``approved`` backlog items. When a repository has none, this module connects
the two without a second planning system: it reuses the capability compiler (IG-060..067),
``compile_capability_authority`` for the repository's declared machine-readable graph,
the strict backlog contract and the development, working and shared memories.

For each repository without a dispatchable item it

1. compiles the graph the repository itself declares at its exact ``origin/main`` head
   (prose is never converted into mutation authority);
2. takes the dependency-ready, clear, non-duplicate capabilities and builds a ``SliceSpec``
   per bounded slice, writing an authoring *request* (objective, acceptance, allowed paths,
   baseline SHA, prior failures and development rules) for a qualified planner;
3. mechanically admits a planner's *draft* only if it satisfies the unchanged backlog
   contract plus roadmap traceability, scope, baseline, level, reservation and duplicate
   checks. An admitted item enters a runtime store beside the backlog state; it is never
   written into the hash-bound release and never marked done: only a merged outcome is.

Anything the compiler cannot decide becomes ``DIRECTION_REQUIRED`` with a focused question
and evidence, and a draft beyond the local builder's measured levels is recorded as a
capability blocker rather than reinterpreted. Every generated, admitted, rejected and
blocked candidate is appended to a bounded ledger and mirrored to working memory.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from scripts.capability_slice_contract import derive_slices

from . import iron_god_backlog as backlog
from . import iron_god_capable_builder as capable_builder
from . import iron_god_development_memory as development_memory
from .capability_authority import CapabilityAuthorityError, compile_capability_authority
from .grant_lifecycle import acquire_lifecycle_lock, release_lifecycle_lock
from . import iron_god_model_routing as model_routing
from .iron_god_calibration import DEFAULT_MODEL, capability_gate, load_profile, load_profiles
from .iron_god_ambiguity_detector import CLEAR, detect_ambiguity
from .iron_god_capability_graph_parser import CapabilityGraphInvalidError, parse_capability_graph
from .iron_god_dependency_resolution import DependencyResolutionError, resolve_dependencies
from .iron_god_duplicate_suppression import SUPPRESSED, suppress_duplicate_work
from .iron_god_slice_decomposition import DECOMPOSED, DIRECTION_REQUIRED, decompose_capability
from .iron_god_slicespec_generator import SliceSpecInputs, generate_slice_spec
from .state_io import atomic_json

ORIGIN = "roadmap-derived"
STORE = "roadmap-refill.json"
REQUESTS = "roadmap-requests"
DRAFTS = "roadmap-drafts"
LEDGER_LIMIT = 500
CHECK_INTERVAL_SECONDS = 20 * 60  # authority is re-read at most this often per repository
BASE_RE = re.compile(r"[0-9a-f]{40}")
LOCAL, CAPABLE, NONE = "LOCAL", "CAPABLE", "NONE"  # who may build a request: the local 7B, a stronger builder, nobody yet


def _now() -> str:
    return datetime.now(UTC).isoformat()


def store_path(lane_root: Path) -> Path:
    return Path(lane_root) / STORE


def load_store(lane_root: Path) -> dict[str, Any]:
    try:
        raw = json.loads(store_path(lane_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    def part(name: str, kind: type) -> Any:
        value = raw.get(name)
        return value if isinstance(value, kind) else kind()

    return {"schema_version": 1, "items": [i for i in part("items", list) if isinstance(i, dict)],
            "ledger": part("ledger", list), "checked": part("checked", dict), "processed": part("processed", dict)}


def _mutate(lane_root: Path, change: Callable[[dict[str, Any]], Any]) -> Any:
    """Serialize every read/modify/write of the store across processes; a crash leaves the last whole file."""
    Path(lane_root).mkdir(parents=True, exist_ok=True)  # the first write on a fresh state root must not crash
    lock = acquire_lifecycle_lock(store_path(lane_root))
    try:
        store = load_store(lane_root)
        result = change(store)
        store["ledger"] = store["ledger"][-LEDGER_LIMIT:]
        atomic_json(store_path(lane_root), store)
        return result
    finally:
        release_lifecycle_lock(lock)


def _log(store: dict[str, Any], kind: str, repository: str, ident: str, detail: Any) -> None:
    store["ledger"].append({"at": _now(), "kind": kind, "repository": repository, "id": ident, "detail": detail})


def admitted_items(lane_root: Path, existing: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Runtime-admitted items that still satisfy the strict contract and do not collide with the release backlog."""
    taken = {str(item.get("id")) for item in existing or []}
    kept: list[dict[str, Any]] = []
    for item in load_store(lane_root)["items"]:
        if str(item.get("id")) in taken or backlog.item_problems(item) or item.get("origin") != ORIGIN:
            continue  # a tampered or colliding entry is never dispatched
        declared = (item.get("authorization") or {}).get("declared_capability_level")
        if type(declared) is not int or item["level"] < declared:
            continue  # the item must prove the slice's declared level and never understate it
        taken.add(str(item["id"]))
        kept.append(item)
    return kept


def id_prefix(repository: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", f"RM-{repository.split('/', 1)[-1]}-")


def item_id(repository: str, capability_id: str, slice_id: str) -> str:
    """Stable identity of a capability's slice; slice ids are only unique within their capability, and the
    digest keeps sanitization or truncation from merging two different slices into one completion state."""
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{id_prefix(repository)}{capability_id}-{slice_id}")[:64]
    digest = hashlib.sha256(json.dumps([repository, capability_id, slice_id]).encode()).hexdigest()[:8]
    return f"{readable}-{digest}"


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out, index = out + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            out, index = out + ".*", index + 2
        elif pattern[index] == "*":
            out, index = out + "[^/]*", index + 1
        elif pattern[index] == "?":
            out, index = out + "[^/]", index + 1
        else:
            out, index = out + re.escape(pattern[index]), index + 1
    return re.compile(out + r"\Z")


def _matches(path: str, patterns: list[str]) -> bool:
    return any(_glob_regex(pattern.replace("\\", "/")).match(path) for pattern in patterns)


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def revisions(base_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if item.get("id") == base_id or str(item.get("id", "")).startswith(base_id + "-r")]


RE_AUTHORABLE = ("FAIL", "REVIEW_REQUIRED")  # outcomes of a worker that ended without delivering the slice
NO_LOCAL_BUILDER_PREFIX = "NO_PYTHON_PACKAGE"  # matches lane NO_PYTHON_PACKAGE_FOR_LOCAL_BUILDER
ESCALATION_SUFFIX = "-cap"
PLANNING_SLICE = "extend-capability-graph"
EXHAUSTED_GRAPH = frozenset({"PRODUCT_COMPLETION_EVIDENCE_REQUIRED", "NO_EXECUTABLE_SLICE"})
OWNER_ROADMAP = re.compile(r"^docs/OWNER_APPROVED_ROADMAP_[A-Za-z0-9._-]+\.md$")
DEFAULT_FORBIDDEN = (".github/**", "private/**", ".env", "**/*.pem", "**/*.key")
MAX_ITEMS_PER_SLICE = 4  # the original item plus three re-authorings; after that the owner decides


FAILURE_EVIDENCE_CHARS = 240
FAILURE_EVIDENCE_LINES = 3
# an error NAMED WITH ITS MESSAGE ("TypeError: unhashable type"), not a bare name or a traceback source line
_ERROR_LINE = re.compile(r"(?<![A-Za-z_])[A-Za-z_]*(?:Error|Exception): [^\n]{1,200}")


def distill_failure(handback: dict[str, Any]) -> str:
    """The distinct error lines of a failed build, bounded: what the next attempt must not repeat.

    Measured 2026-11-13 (rocksmith section-agreement): four attempts failed, the last two on the same
    ``TypeError: unhashable type: 'list'``, because a re-authored request carried only ``FAIL: SLICE_TESTS_FAILED``.
    The engine already holds the evidence in its own handback; this reads it, deterministically, with no model.
    """
    parts = [str(handback.get("first_test_failure") or "")]
    parts += [str(row.get("output_tail") or "") for row in handback.get("verification") or []
              if isinstance(row, dict) and not row.get("ok")]
    seen: list[str] = []
    for match in _ERROR_LINE.finditer("\n".join(parts)):
        line = " ".join(match.group(0).split())
        stem = line.removesuffix("...")  # pytest shortens the same error in its summary line
        if not any(known.startswith(stem) for known in seen):
            seen.append(line)
    return "; ".join(seen[:FAILURE_EVIDENCE_LINES])[:FAILURE_EVIDENCE_CHARS]


def attach_failure_evidence(lane_root: Path, state: dict[str, Any], requests: list[dict[str, Any]]) -> None:
    """Give every re-authored request the error its earlier attempts actually hit (their newest readable handback)."""
    for request in requests:
        for row in request.get("prior_failures") or []:
            if not isinstance(row, dict) or row.get("evidence"):
                continue
            packets = state.get("items", {}).get(str(row.get("id")), {}).get("packets") or []
            for packet in reversed([p for p in packets if isinstance(p, str)]):
                try:
                    handback = json.loads((Path(lane_root) / "handbacks" / f"{packet}.json").read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    continue
                evidence = distill_failure(handback) if isinstance(handback, dict) else ""
                if evidence:
                    row["evidence"] = evidence
                    break


def failure_lessons(request: dict[str, Any]) -> list[str]:
    """The distinct failure evidence a re-authored request carries, oldest first."""
    lessons: list[str] = []
    for row in request.get("prior_failures") or []:
        text = str(row.get("evidence") or "") if isinstance(row, dict) else ""
        if text and text not in lessons:
            lessons.append(text)
    return lessons


def _slice_status(base_id: str, items: list[dict[str, Any]], state: dict[str, Any],
                  delivered: frozenset[str] = frozenset()) -> tuple[str, str]:
    """(NEW|PENDING|DONE|FAILED|EXHAUSTED, id), from the lane's own record and never from the graph.

    ``delivered`` are request ids whose capable-builder pull request was merged; that is completion too.
    """
    if f"{base_id}{ESCALATION_SUFFIX}" in delivered:  # the capable builder's escalation of an exhausted slice merged
        return "DONE", f"{base_id}{ESCALATION_SUFFIX}"
    chain = revisions(base_id, items)
    if not chain:
        if any(ident == base_id or ident.startswith(base_id + "-r") for ident in delivered):
            return "DONE", base_id
        return "NEW", base_id
    latest = chain[-1]
    row = state.get("items", {}).get(latest["id"], {})
    if backlog.is_done(latest, state):
        return "DONE", latest["id"]
    limit = backlog.DIRECT_ATTEMPTS if latest.get("direct") else backlog.MAX_ITEM_ATTEMPTS
    # 2026-11-02 (VoiceStudio S2): an item that spent every attempt on REVIEW_REQUIRED stayed PENDING forever, because only
    # a FAIL outcome counted as failed. An environment failure is not re-authored (the plan was not at fault), and an
    # in-flight or conflict-held item is not finished; the old item's record is never touched or reset.
    # An item whose last two attempts failed on byte-identical source is never dispatched again (backlog.next_item), so
    # it is as finished as an item with no attempts left (VoiceStudio S2-r1, 2026-11-03: stuck at 2 of 3 attempts).
    out_of_attempts = row.get("attempts", 0) >= limit or backlog.repeated_failed_source(row)
    failed = str(row.get("outcome", "")).startswith(RE_AUTHORABLE) and out_of_attempts
    if row.get("outcome") not in backlog.IN_FLIGHT and backlog.lineage_capped(state, latest["id"]):
        # the same step keeps failing its tests (even if its newest repair never started): stop re-authoring, the owner decides
        return "EXHAUSTED", latest["id"]
    if failed and len(chain) >= MAX_ITEMS_PER_SLICE:
        return "EXHAUSTED", latest["id"]  # terminal failure must reach the durable owner-decision ledger
    if failed or latest.get("status") == "retired":
        return "FAILED", f"{base_id}-r{len(chain)}"
    return "PENDING", latest["id"]


def with_lane_progress(repository: str, payload: dict[str, Any], items: list[dict[str, Any]],
                       state: dict[str, Any], delivered: frozenset[str] = frozenset()) -> dict[str, Any]:
    """A copy of the graph in which slices the lane itself has merged count as VERIFIED_COMPLETE.

    The lane's record (an item whose outcome is MERGED through its own preflight, CI and merge gates) is the
    proof; nothing is inferred from a pull request title. This lets the next dependent slice become ready right
    after a merge, before any durable graph update lands, and never marks a capability complete: a capability
    with no slice left asks for direction instead. A slice whose capable-builder pull request was merged
    (``delivered``) counts the same way, with that pull request as the evidence.
    """
    copy = json.loads(json.dumps(payload))
    for capability in copy.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        slices = capability.get("slices")
        for row in slices if isinstance(slices, list) else []:  # a null/non-list collection keeps its direction request
            if not isinstance(row, dict) or row.get("state") == "VERIFIED_COMPLETE":
                continue
            status, ident = _slice_status(item_id(repository, str(capability.get("id")), str(row.get("id"))), items,
                                          state, delivered)
            if status == "DONE":
                outcome = state.get("items", {}).get(ident, {})
                row["state"] = "VERIFIED_COMPLETE"
                row["verification"] = [
                    f"lane item {ident} merged through preflight, CI and merge gates "
                    f"(packet {outcome.get('packet_id', 'unrecorded')})" if outcome else
                    f"capable-builder pull request for {ident} merged on GitHub"]
    return copy


def derive(repository: str, head: str, payload: dict[str, Any], source: str, *, items: list[dict[str, Any]],
           state: dict[str, Any], repo_root: Path | None = None, milestones: dict[str, str] | None = None,
           external_pending: frozenset[str] = frozenset(), external_done: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Run the existing compiler over the graph; returns requests plus what was skipped or needs direction.

    Slice selection is the supervisor's own ``derive_slices`` contract (roadmap order, slice dependencies,
    objective and acceptance required, product-completion proof), so both dispatch paths see identical slices.
    """
    result: dict[str, Any] = {"requests": [], "direction_required": [], "skipped": []}
    try:
        graph = parse_capability_graph(payload)
    except CapabilityGraphInvalidError as exc:
        result["direction_required"].append({
            "reason": "CAPABILITY_GRAPH_INVALID", "evidence": str(exc)[:600],
            "question": f"{source} fails validation; correct the declared capability graph so the engine can plan from it."})
        return result
    payload = with_lane_progress(repository, payload, items, state, external_done)
    derived = derive_slices(payload, repository=repository, exact_head=head)
    if derived["status"] == "NO_WORK":
        result["skipped"].append({"reason": derived.get("reason") or "NO_WORK"})
        return result
    if derived["status"] != "READY":
        codes = [str(issue.get("code")) for issue in derived.get("issues", []) if isinstance(issue, dict)]
        result["direction_required"].append({
            "reason": codes[0] if codes else str(derived["status"]), "evidence": derived.get("issues", []),
            "question": f"{source} has no executable next slice ({', '.join(codes) or derived['status']}); "
                        "what bounded slice should advance the approved roadmap outcome?"})
        return result
    capability_id = derived["slices"][0]["capability_id"]
    node = next(n for n in graph.nodes if n.capability_id == capability_id)
    decomposition = decompose_capability(node)
    try:
        resolutions = (resolve_dependencies(repo_root, capability_id, node.depends_on,
                                            milestone_id_by_capability=milestones) if repo_root and milestones else ())
    except DependencyResolutionError as exc:
        result["direction_required"].append({
            "capability": capability_id, "reason": "DEPENDENCY_UNMAPPED", "evidence": str(exc)[:400],
            "question": f"Which milestone proves the dependencies of {capability_id}?"})
        return result
    ambiguity = detect_ambiguity(decomposition, resolutions)
    if decomposition.status != DECOMPOSED or ambiguity.status != CLEAR:
        result["direction_required" if decomposition.status in {DIRECTION_REQUIRED, DECOMPOSED} else "skipped"].append(
            {"capability": capability_id, "reason": ambiguity.reason if decomposition.status == DECOMPOSED
             else decomposition.reason, "evidence": list(ambiguity.unresolved_dependencies),
             "question": f"Capability {capability_id} is blocked or ambiguous in {source}; what is the intended next step?"}
            if decomposition.status in {DIRECTION_REQUIRED, DECOMPOSED} else {"capability": capability_id,
                                                                              "reason": decomposition.status})
        return result
    declared = {(cap.get("id"), row.get("id")): row for cap in payload.get("capabilities", []) if isinstance(cap, dict)
                for row in cap.get("slices", []) if isinstance(row, dict)}
    candidates = []
    for chosen in derived["slices"]:
        base_id = item_id(repository, capability_id, str(chosen["slice_id"]))
        status, expected = _slice_status(base_id, items, state, external_done)
        if status == "EXHAUSTED":
            chain = revisions(base_id, items)
            failures = []
            for item in chain:
                row = state.get("items", {}).get(item["id"], {})
                # Only the outcome class is safe to persist in the owner-decision ledger.
                outcome = str(row.get("outcome", ""))
                outcome_class = "REVIEW_REQUIRED" if outcome.startswith("REVIEW_REQUIRED") else "FAIL"
                attempts = row.get("attempts", 0)
                safe_attempts = attempts if type(attempts) is int else 0
                failures.append(f"{item['id']}: {outcome_class} after {safe_attempts} attempts")
            result["direction_required"].append({
                "capability": capability_id, "slice": chosen["slice_id"],
                "reason": "REAUTHOR_LIMIT_REACHED", "id": expected, "evidence": failures,
                "question": f"Slice {chosen['slice_id']} exhausted {len(chain)} revisions; "
                            "should its scope change, a different builder be used, or the slice be retired?",
            })
            continue
        if status in {"PENDING", "DONE"}:
            result["skipped"].append({"capability": capability_id, "slice": chosen["slice_id"], "reason": status, "id": expected})
            continue
        if expected in external_pending:  # a stronger builder is already working on exactly this request
            result["skipped"].append({"capability": capability_id, "slice": chosen["slice_id"],
                                      "reason": "PENDING_CAPABLE_BUILDER", "id": expected})
            continue
        if not chosen["allowed_paths"]:
            result["direction_required"].append({
                "capability": capability_id, "slice": chosen["slice_id"], "reason": "ALLOWED_PATHS_MISSING", "evidence": [],
                "question": f"Slice {chosen['slice_id']} names no allowed paths; which files may it change?"})
            continue
        spec = generate_slice_spec(expected, decomposition, ambiguity, SliceSpecInputs(
            repository=repository, exact_baseline_sha=head, allowed_paths=tuple(chosen["allowed_paths"]),
            forbidden_paths=tuple(chosen["forbidden_paths"]), runner_requirements=tuple(chosen["runner_requirements"]),
            runtime_requirements=tuple(chosen["runtime_requirements"]), resource_requirements=(),
            product_reality_requirements=(), expected_artifacts=(), risk_class="standard",
            verification_policy="canonical-preflight-and-ci", estimated_conflict_scope=tuple(chosen["allowed_paths"])))
        candidates.append((spec, chosen, status, base_id))
        break  # one slice per capability at a time; the rest wait for it to merge
    verdicts = suppress_duplicate_work(tuple(spec for spec, *_rest in candidates))
    for (spec, chosen, status, base_id), verdict in zip(candidates, verdicts, strict=True):
        if verdict.status == SUPPRESSED:
            result["skipped"].append({"capability": spec.capability_id, "reason": "DUPLICATE", "id": spec.slice_id})
            continue
        failures = [{"id": item["id"], "outcome": state.get("items", {}).get(item["id"], {}).get("outcome"),
                     "attempts": state.get("items", {}).get(item["id"], {}).get("attempts")}
                    for item in revisions(base_id, items)] if status == "FAILED" else []
        result["requests"].append({
            "schema_version": 1, "id": spec.slice_id, "repository": repository, "exact_head": head,
            "roadmap_ref": f"{source}#{spec.capability_id}/{chosen['slice_id']}", "capability": spec.capability_id,
            "slice": chosen["slice_id"], "objective": chosen["objective"], "slice_acceptance": list(chosen["acceptance"]),
            "completion_evidence": list(spec.acceptance_criteria), "allowed_paths": list(spec.allowed_paths),
            "forbidden_paths": list(spec.forbidden_paths), "runtime_requirements": list(spec.runtime_requirements),
            "runner_requirements": list(spec.runner_requirements), "prior_failures": failures,
            "capability_level": declared.get((capability_id, chosen["slice_id"]), {}).get("capability_level"),
            "acceptance_examples": declared.get((capability_id, chosen["slice_id"]), {}).get("acceptance_examples", []),
            "directed_edit": declared.get((capability_id, chosen["slice_id"]), {}).get("directed_edit"),
            "cpp": declared.get((capability_id, chosen["slice_id"]), {}).get("cpp"),
            "max_builder_level": max(backlog.BUILDABLE_LEVELS),
            "needs": "a backlog item (level 1 build spec, or level 2 directed edit with approved tests that fail at "
                     "exact_head) whose id and roadmap_ref equal this request's, written to the drafts directory"})
    return result


LOCAL_LOOKAHEAD_CAPABILITIES = 4  # how many roadmap capabilities the local lane may look down past a slice it cannot build


def can_start_locally(request: dict[str, Any]) -> bool:
    """True when the request is routed LOCAL **and** the local drafter can actually take it.

    Routing says which builder is allowed; it does not say the local lane can start. A Level 2 slice with no declared directed
    edit, or a Level 1 slice with fewer than two independently authored examples, routes LOCAL yet is never drafted, and
    because it routes LOCAL the capable builder never receives it (observed 2026-11-12: knowledge-engine-ai's contract-fixture
    slice, declared Level 2 with no directed edit, sat "in progress" for hours with both builders idle).
    """
    from . import iron_god_local_drafter as local_drafter  # local import: the drafter builds on this module

    return (request.get("route") or {}).get("builder") == LOCAL and local_drafter.eligible(request)


def local_lookahead(repository: str, head: str, payload: dict[str, Any], source: str, *, items: list[dict[str, Any]],
                    state: dict[str, Any], route_request: Callable[[dict[str, Any]], dict[str, Any]], skip: set[str],
                    can_take: Callable[[dict[str, Any]], bool] = lambda _request: True,
                    external_pending: frozenset[str] = frozenset(), external_done: frozenset[str] = frozenset()
                    ) -> list[dict[str, Any]]:
    """Slices further down the roadmap that the LOCAL builder may take while the head of the line waits for a stronger one.

    The compiler yields work for one capability at a time, in roadmap order. When that slice needs the capable builder, the
    repository would offer the free local builder nothing, however many small slices sit behind it. This looks past it: each
    capability already offered is set aside, the compiler is asked again, and only the requests that route LOCAL are kept (a
    request that routes elsewhere is set aside too and the search goes on, up to ``LOCAL_LOOKAHEAD_CAPABILITIES``).
    Nothing is reordered, no gate is relaxed, a capability that depends on an unfinished one is still not offered, and the
    head slice keeps its place for the stronger builder.
    """
    skipped = set(skip)
    found: list[dict[str, Any]] = []
    for _ in range(LOCAL_LOOKAHEAD_CAPABILITIES):
        # The graph contract requires the roadmap to list every unfinished capability, so none is dropped: the ones already
        # offered are moved behind the rest, which makes the compiler pick the next one in order.
        order = list(payload.get("roadmap", []))
        roadmap = [c for c in order if c not in skipped] + [c for c in order if c in skipped]
        derived = derive(repository, head, {**payload, "roadmap": roadmap}, source, items=items, state=state,
                         external_pending=external_pending, external_done=external_done)
        fresh = [request for request in derived["requests"] if str(request.get("capability")) not in skipped]
        # A capability whose request is already open in the capable lane (or already done) yields no request but is reported
        # under ``skipped``: set it aside too and keep looking, or the search would pick it again and stop.
        passed_over = {str(row["capability"]) for row in derived.get("skipped", []) if row.get("capability")} - skipped
        skipped |= passed_over
        if not fresh and not passed_over:
            break
        for request in fresh:
            skipped.add(str(request["capability"]))
            if route_request(request).get("builder") == LOCAL and can_take(request):
                found.append(request)
    return found


def route(request: dict[str, Any], *, profile: dict[str, Any] | None, max_level: int,
          unsupported: str | None = None, expected_model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """Which builder may take a request, from declared level + the measured profile + the lane's buildable levels.

    The local worker is only assigned work inside what was measured: the slice must declare ``capability_level``,
    the calibration profile must match this model and hardware and pass that level, and the product lane must
    build that level. Anything else goes to a stronger builder or nobody, never silently simplified.
    """
    level = request.get("capability_level")
    if unsupported:
        # A repository the local builder cannot build at all (no Python package: Everward, 2026-11-03) is exactly what
        # the stronger builder is for; only a repository nobody can build for stays unassigned.
        builder = CAPABLE if unsupported.startswith(NO_LOCAL_BUILDER_PREFIX) else NONE
        return {"builder": builder, "reason": unsupported, "level": level}
    if request.get("force_builder") == CAPABLE:  # an exhausted local slice or a planning task: a stronger builder only
        return {"builder": CAPABLE, "reason": str(request.get("force_reason") or "STRONGER_BUILDER_REQUIRED"), "level": level}
    gate = capability_gate(profile, {"capability_level": level}, model=expected_model)
    if gate is not None:
        # An undeclared or uncalibrated level is not evidence the local builder can do it.
        return {"builder": CAPABLE, "reason": gate, "level": level}
    limit = min(max_level, max(backlog.BUILDABLE_LEVELS))
    if level not in backlog.BUILDABLE_LEVELS or level > limit:
        return {"builder": CAPABLE, "reason": "LEVEL_BEYOND_PRODUCT_LANE", "level": level, "lane_limit": limit}
    return {"builder": LOCAL, "reason": "WITHIN_MEASURED_LOCAL_CAPABILITY", "level": level, "lane_limit": limit}


def draft_problems(draft: Any, request: dict[str, Any], *, items: list[dict[str, Any]], reserved: set[str],
                   head: str, changed_since: Callable[[str, str], set[str]], max_level: int) -> list[str]:
    """Why a planner's draft may not enter the backlog (empty when it may)."""
    if not isinstance(draft, dict):
        return ["DRAFT_NOT_AN_OBJECT"]
    candidate = {**draft, "status": "approved", "assignee": backlog.AGENT_ID}
    problems = list(backlog.item_problems(candidate))
    if draft.get("id") != request["id"]:
        problems.append("ID_MISMATCH")
    if draft.get("repository") != request["repository"]:
        problems.append("REPOSITORY_MISMATCH")
    if draft.get("roadmap_ref") != request["roadmap_ref"]:
        problems.append("ROADMAP_REF_MISMATCH")
    addresses = str(draft.get("addresses", ""))
    if request["capability"] not in addresses and str(request["slice"]) not in addresses:
        problems.append("NOT_TRACEABLE_TO_ROADMAP_OUTCOME")
    if type(draft.get("level")) is int and (draft["level"] not in backlog.BUILDABLE_LEVELS or draft["level"] > max_level):
        problems.append("LEVEL_BEYOND_LOCAL_BUILDER")
    # Admission feeds the LOCAL builder's queue, so the request itself must have been routed there, and the draft
    # may not understate the slice's declared level (that would be silently simplifying the work).
    route = request.get("route")
    if not isinstance(route, dict) or route.get("builder") != LOCAL:
        problems.append("REQUEST_NOT_ROUTED_TO_LOCAL_BUILDER")
    declared_level = request.get("capability_level")
    if type(draft.get("level")) is int and (type(declared_level) is not int or draft["level"] < declared_level):
        problems.append("DRAFT_UNDERSTATES_DECLARED_LEVEL")
    files = backlog.edit_files(candidate)
    if not files:
        problems.append("NO_EDIT_FILES")
    if any(not _matches(path, request["allowed_paths"]) or _matches(path, request["forbidden_paths"]) for path in files):
        problems.append("PATH_OUTSIDE_ROADMAP_SCOPE")
    if files & reserved:
        problems.append("FILE_RESERVED_BY_OPEN_WORK")
    if candidate.get("direct"):
        base = str(draft.get("approved_base") or "")
        if not BASE_RE.fullmatch(base):
            problems.append("APPROVED_BASE_MISSING")
        elif base != head:
            try:
                if changed_since(base, head) & files:
                    problems.append("APPROVED_BASE_STALE")
            except Exception:  # an unreadable history is not evidence that the base is current
                problems.append("APPROVED_BASE_UNVERIFIABLE")
    superseded = {str(row.get("id")) for row in request.get("prior_failures", []) if isinstance(row, dict)}
    objective = " ".join(str(draft.get("objective", "")).split()).lower()
    target = (candidate["direct"].get("module_path"), sorted(candidate["direct"].get("targets") or [])) \
        if isinstance(candidate.get("direct"), dict) else None
    for other in items:
        if other.get("repository") != request["repository"] or other.get("status") == "retired":
            continue
        if other.get("id") in superseded:
            continue  # a failed predecessor of this very revision: re-authoring it is the point, not a duplicate
        other_direct = other.get("direct") if isinstance(other.get("direct"), dict) else None
        if " ".join(str(other.get("objective", "")).split()).lower() == objective:
            problems.append("DUPLICATE_OBJECTIVE")
        elif target and other_direct and (other_direct.get("module_path"), sorted(other_direct.get("targets") or [])) == target:
            problems.append("DUPLICATE_TARGET")
    return sorted(set(problems))


def stamp(draft: dict[str, Any], request: dict[str, Any], source: str) -> dict[str, Any]:
    item = {**draft, "status": "approved", "assignee": backlog.AGENT_ID, "origin": ORIGIN}
    item["authorization"] = {
        "basis": "standing owner-approved roadmap (development-scope delegation 2026-11-01, issue #594)",
        "authority": source, "exact_head": request["exact_head"], "capability": request["capability"],
        "slice": request["slice"], "declared_capability_level": request.get("capability_level"),
        "route": (request.get("route") or {}).get("builder"),
        "grants": "development only; no execution, release or control authority"}
    return item


def _git_changed(clone: Path) -> Callable[[str, str], set[str]]:
    def changed(base: str, head: str) -> set[str]:
        out = subprocess.run(["git", "diff", "--name-only", f"{base}..{head}"], cwd=clone, capture_output=True,
                             text=True, check=True, timeout=120)
        return {line.strip().replace("\\", "/") for line in out.stdout.splitlines() if line.strip()}
    return changed


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def process_drafts(lane_root: Path, repository: str, requests: list[dict[str, Any]], *, items: list[dict[str, Any]],
                   reserved: set[str], head: str, changed_since: Callable[[str, str], set[str]], max_level: int,
                   source: str) -> dict[str, list[str]]:
    """Admit or reject each draft file once per distinct content; unchanged drafts are not re-judged."""
    outcome: dict[str, list[str]] = {"admitted": [], "rejected": [], "blocked": []}
    for request in requests:
        path = Path(lane_root) / DRAFTS / f"{request['id']}.json"
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        draft = _read_json(path)

        def judge(store: dict[str, Any], request: dict[str, Any] = request, digest: str = digest, draft: Any = draft) -> None:
            if store["processed"].get(request["id"]) == digest:
                return
            ids = {item.get("id") for item in items}
            known = [*items, *[i for i in store["items"] if i.get("id") not in ids]]
            problems = draft_problems(draft, request, items=known, reserved=reserved, head=head,
                                      changed_since=changed_since, max_level=max_level)
            store["processed"][request["id"]] = digest
            if not problems:
                store["items"].append(stamp(draft, request, source))
                _log(store, "ADMITTED", repository, request["id"], {"roadmap_ref": request["roadmap_ref"]})
                outcome["admitted"].append(request["id"])
            elif "LEVEL_BEYOND_LOCAL_BUILDER" in problems:
                _log(store, "CAPABILITY_BLOCKER", repository, request["id"],
                     {"problems": problems, "route": "a capable, separately authorized builder"})
                outcome["blocked"].append(request["id"])
            else:
                _log(store, "REJECTED", repository, request["id"], {"problems": problems})
                outcome["rejected"].append(request["id"])

        _mutate(lane_root, judge)
    return outcome


def owner_roadmap(show: Callable[[str, str, str], str], listing: list[str]) -> tuple[str, str] | None:
    """(path, text) of the repository's owner-approved roadmap at the exact head when it carries the standing grant.

    The grant is read from the document itself: an OWNER-APPROVED status line and the standing-authorization section.
    Anything less is not authority, and the caller keeps asking the owner.
    """
    for path in sorted((p for p in listing if OWNER_ROADMAP.fullmatch(p)), reverse=True):
        try:
            text = show("", "", path)
        except Exception:  # an unreadable roadmap is not authority
            continue
        if re.search(r"^Status:\s*OWNER-APPROVED", text, re.M) and "standing development authorization" in text.lower():
            return path, text
    return None


def _next_slices(text: str) -> str:
    match = re.search(r"^##\s+Next slices\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return " ".join(match.group(1).split())[:1200] if match else ""


def _graph_shape(payload: dict[str, Any]) -> list[str]:
    return sorted(f"{cap.get('id')}:{cap.get('state')}:" + ",".join(
        f"{row.get('id')}={row.get('state')}" for row in cap.get("slices") or [] if isinstance(row, dict))
        for cap in payload.get("capabilities", []) if isinstance(cap, dict))


def planning_request(repository: str, head: str, source: str, payload: dict[str, Any], path: str, text: str) -> dict[str, Any]:
    """Ask the capable builder to extend the capability graph from the owner roadmap instead of asking the owner.

    The id changes only when the graph's shape or the roadmap text changes, so one open plan owns the repository until
    its pull request lands (which changes the graph). Fences are inherited from the graph's own slices, never widened.
    """
    forbidden = sorted({str(p) for cap in payload.get("capabilities", []) if isinstance(cap, dict)
                        for row in cap.get("slices") or [] if isinstance(row, dict) for p in row.get("forbidden_paths") or []}
                       | set(DEFAULT_FORBIDDEN))
    digest = hashlib.sha256(json.dumps([path, hashlib.sha256(text.encode()).hexdigest(), _graph_shape(payload)]).encode()).hexdigest()[:8]
    ident = f"{id_prefix(repository)}plan-{digest}"
    objective = (
        f"Every slice in {source} is complete, but the owner-approved roadmap {path} (standing development "
        "authorization; no per-slice approval is required) still lists unfinished outcomes. Plan the next work from "
        f"evidence: read {path}, the current source and merged history at the baseline. Its 'Next slices' are starting "
        f"points, not a ceiling: {_next_slices(text)}. Skip any item already delivered (cite the merged evidence). "
        f"Edit {source} so the next work becomes executable, choosing by the graph's shape. If a capability is still PLANNED but "
        "all its slices are VERIFIED_COMPLETE (its outcome is not achieved), append the new slices to THAT capability; the "
        "compiler selects the first unfinished capability in roadmap order and ignores later ones. Only if every capability "
        "is VERIFIED_COMPLETE with verification evidence, append a new capability and set the roadmap list to it. Never mark a "
        "capability complete without evidence, and never edit or remove an existing slice or its evidence. Each new slice has "
        "objective, acceptance, allowed_paths, forbidden_paths, runner_requirements, depends_on where ordered, and a truthful "
        "capability_level (1 = one function with examples, 2 = directed edit with failing approved tests, 3+ = anything larger; "
        "never understate to reach a cheaper builder). Choose free, local libraries already used by the repository. Change only "
        f"{source}. If the roadmap cannot be decomposed without a decision with significant consequences, open no "
        "pull request and report one focused question.")
    return {"schema_version": 1, "id": ident, "repository": repository, "exact_head": head,
            "roadmap_ref": f"{path}#next-slices", "capability": "roadmap-planning", "slice": PLANNING_SLICE,
            "objective": objective,
            "slice_acceptance": [f"{source} validates and, run through the capability compiler, yields at least one executable slice "
                                 f"traceable to {path}",
                                 "no existing slice or evidence is edited or removed, and no capability is marked complete without evidence",
                                 "every new slice declares an honest capability_level and inherits the forbidden paths"],
            "completion_evidence": [f"merged pull request changing only {source}"],
            "allowed_paths": [source], "forbidden_paths": forbidden, "runtime_requirements": [],
            "runner_requirements": ["code", "local", "offline"], "prior_failures": [], "capability_level": None,
            "acceptance_examples": [], "directed_edit": None, "max_builder_level": max(backlog.BUILDABLE_LEVELS),
            "force_builder": CAPABLE, "force_reason": "ROADMAP_PLANNING_TASK"}


def escalation_request(repository: str, head: str, source: str, payload: dict[str, Any], row: dict[str, Any],
                       failures: list[Any]) -> dict[str, Any] | None:
    """One capable-builder request for a slice whose local revisions are all spent (None if the graph lost the slice)."""
    declared = next((s for cap in payload.get("capabilities", []) if isinstance(cap, dict) and cap.get("id") == row.get("capability")
                     for s in cap.get("slices") or [] if isinstance(s, dict) and s.get("id") == row.get("slice")), None)
    if not declared or not declared.get("allowed_paths"):
        return None
    base = item_id(repository, str(row["capability"]), str(row["slice"]))
    return {"schema_version": 1, "id": f"{base}{ESCALATION_SUFFIX}", "repository": repository, "exact_head": head,
            "roadmap_ref": f"{source}#{row['capability']}/{row['slice']}", "capability": row["capability"], "slice": row["slice"],
            "objective": f"{declared.get('objective', '')} (Escalated: the local builder spent every revision on this slice; "
                         "take a materially different approach from prior_failures.)",
            "slice_acceptance": list(declared.get("acceptance") or []),
            "completion_evidence": list(declared.get("acceptance") or []),
            "allowed_paths": list(declared["allowed_paths"]), "forbidden_paths": list(declared.get("forbidden_paths") or []),
            "runtime_requirements": list(declared.get("runtime_requirements") or []),
            "runner_requirements": list(declared.get("runner_requirements") or []),
            "prior_failures": [{"id": str(text).split(":")[0], "outcome": str(text)} for text in failures],
            "capability_level": declared.get("capability_level"), "acceptance_examples": [], "directed_edit": None,
            "max_builder_level": max(backlog.BUILDABLE_LEVELS), "force_builder": CAPABLE,
            "force_reason": "LOCAL_BUILDER_EXHAUSTED"}


def continue_without_owner(rows: list[dict[str, Any]], *, repository: str, head: str, source: str, payload: dict[str, Any],
                           roadmap: tuple[str, str] | None, pending: frozenset[str], done: frozenset[str],
                           spent: frozenset[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(new requests, questions that still need the owner, notes) for direction rows the roadmap already answers.

    Only two situations are answerable from approved scope: the graph ran out of slices while the owner roadmap
    (carrying the standing grant) is unfinished, and a slice used every local revision. Everything else, and every
    case where the capable attempts are spent, stays an owner question: nothing is invented and no gate is skipped.
    """
    requests: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for row in rows:
        request = None
        if row.get("reason") in EXHAUSTED_GRAPH and roadmap:
            request = planning_request(repository, head, source, payload, *roadmap)
        elif row.get("reason") == "REAUTHOR_LIMIT_REACHED" and row.get("slice"):
            request = escalation_request(repository, head, source, payload, row, list(row.get("evidence") or []))
        if request is None:
            remaining.append(row)
        elif request["id"] in done or request["id"] in pending:
            notes.append({"id": request["id"], "reason": "DONE" if request["id"] in done else "PENDING_CAPABLE_BUILDER"})
        elif request["id"] in spent:
            remaining.append({**row, "evidence": [*(row.get("evidence") or []), f"{request['id']}: capable attempts spent"]})
        else:
            requests.append(request)
    return requests, remaining, notes


def refill_repository(lane_root: Path, state_root: Path, repository: str, clone: Path, head: str, *,
                      items: list[dict[str, Any]], state: dict[str, Any], reserved: set[str], max_level: int,
                      changed_since: Callable[[str, str], set[str]] | None = None,
                      unsupported: Callable[[Path, str], str | None] | None = None) -> dict[str, Any]:
    """One planning pass for a repository without a dispatchable item; bad authority becomes DIRECTION_REQUIRED."""
    changed_since = changed_since or _git_changed(clone)

    def show(_repo: str, _head: str, path: str) -> str:
        proc = subprocess.run(["git", "show", f"{head}:{path}"], cwd=clone, capture_output=True, text=True,
                              check=False, timeout=60)
        if proc.returncode:
            raise CapabilityAuthorityError(f"AUTHORITY_FILE_UNAVAILABLE:{path}")
        return proc.stdout

    report: dict[str, Any] = {"repository": repository, "exact_head": head, "requests": [], "admitted": [],
                              "rejected": [], "blocked": [], "direction_required": [], "skipped": [],
                              "routed": {LOCAL: [], CAPABLE: [], NONE: []}}
    source = ""
    payload: dict[str, Any] | None = None
    try:
        from . import iron_god_task_decomposer as decomposer  # local import: the decomposer builds on this module

        decomposer.settle(lane_root, items, state)
        control_plane = json.loads(show(repository, head, "engineering/control-plane.json"))
        compiled = compile_capability_authority(state_root, repository, head, control_plane, read_authoritative_file=show)
        source = compiled["graph"]["authority"]["source"]
        payload = compiled["graph"]
        derived = derive(repository, head, compiled["graph"], source, items=items, state=state,
                         external_pending=frozenset(capable_builder.open_request_ids(lane_root))
                         | decomposer.active_parent_ids(lane_root),
                         external_done=frozenset(capable_builder.merged_request_ids(lane_root))
                         | decomposer.done_parent_ids(lane_root))
        derived["requests"].extend(decomposer.child_requests(lane_root, repository, head, items, state))
    except (CapabilityAuthorityError, ValueError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc)[:300] or type(exc).__name__
        derived = {"requests": [], "skipped": [], "direction_required": [{
            "reason": reason.split(":")[0] or "ROADMAP_AUTHORITY_UNAVAILABLE", "evidence": reason,
            "question": "Declare this repository's machine-readable capability graph (engineering/control-plane.json "
                        "capability_graph, listed in authoritative_documents) from its owner-approved roadmap, or say "
                        "which document is the authority."}]}
    if payload is not None and derived["direction_required"]:
        try:
            listing = subprocess.run(["git", "ls-tree", "--name-only", head, "docs/"], cwd=clone, capture_output=True,
                                     text=True, check=False, timeout=60).stdout.split()
            roadmap = owner_roadmap(show, listing)
            extra, derived["direction_required"], notes = continue_without_owner(
                derived["direction_required"], repository=repository, head=head, source=source, payload=payload,
                roadmap=roadmap, pending=frozenset(capable_builder.open_request_ids(lane_root)),
                done=frozenset(capable_builder.merged_request_ids(lane_root)),
                spent=frozenset(capable_builder.spent_request_ids(lane_root)))
            derived["requests"].extend(extra)
            derived["skipped"].extend(notes)
        except Exception:  # continuing is an optimisation; failing it leaves the owner question exactly as it was
            pass
    try:
        rules = development_memory.rules_for(lane_root, repository)
    except Exception:  # memory is advisory evidence for the planner, never a gate
        rules = []
    # "can any local model ever do this?" is answered by the best valid profile, never by what fits in memory this minute
    best = model_routing.best_profile(load_profiles(state_root))
    profile = best or load_profile(state_root)
    expected_model = best["model"] if best else DEFAULT_MODEL  # only a validated best profile may stand for a non-default model
    try:
        repo_problem = unsupported(clone, head) if unsupported else None
    except Exception as exc:  # an unreadable tree is not evidence the repository can be built
        repo_problem = f"REPOSITORY_UNREADABLE: {type(exc).__name__}"
    def route_request(request: dict[str, Any]) -> dict[str, Any]:
        request["development_rules"] = rules
        request["graph_path"] = source  # the declared graph: the one file any capable session may also edit to record delivery
        request["route"] = route(request, profile=profile, max_level=max_level, unsupported=repo_problem,
                                 expected_model=expected_model)
        return request["route"]

    for request in derived["requests"]:
        route_request(request)
        report["routed"][request["route"]["builder"]].append(request["id"])
    report["local_lookahead"] = []
    if payload is not None and not repo_problem and not any(can_start_locally(r) for r in derived["requests"]):
        # The head of the line needs a stronger builder (above Level 2, or its budget is spent): the free local builder
        # would be offered nothing from this repository however many small slices sit behind it. Look past it.
        try:
            from . import iron_god_task_decomposer as decomposer_lookahead

            extra = local_lookahead(
                repository, head, payload, source, items=items, state=state, route_request=route_request,
                can_take=can_start_locally,
                skip={str(r["capability"]) for r in derived["requests"] if r.get("capability")}
                | {str(row["capability"]) for row in derived["skipped"] if row.get("capability")},
                external_pending=frozenset(capable_builder.open_request_ids(lane_root))
                | decomposer_lookahead.active_parent_ids(lane_root),
                external_done=frozenset(capable_builder.merged_request_ids(lane_root))
                | decomposer_lookahead.done_parent_ids(lane_root))
        except Exception:  # the lookahead is an optimisation: failing it leaves the head-of-line behaviour exactly as it was
            extra = []
        for request in extra:
            derived["requests"].append(request)
            report["routed"][LOCAL].append(request["id"])
        report["local_lookahead"] = [request["id"] for request in extra]
    for request in derived["requests"]:
        # Open pull requests own files; a capable slice may touch anything its allowed paths cover.
        # The graph is part of the effective scope: a capable session may edit it to record an already-delivered slice.
        scope = [*request["allowed_paths"], *([source] if source else [])]
        request["reserved_conflicts"] = sorted(path for path in reserved if _matches(path, scope))
    report["capable_requests"] = [request for request in derived["requests"] if request["route"]["builder"] == CAPABLE]
    report["local_requests"] = [request for request in derived["requests"] if request["route"]["builder"] == LOCAL]
    report["direction_required"], report["skipped"] = derived["direction_required"], derived["skipped"]
    report["requests"] = [request["id"] for request in derived["requests"]]
    try:
        attach_failure_evidence(lane_root, state, derived["requests"])
    except Exception:  # evidence helps the next attempt; never a reason to stop planning
        pass
    requests_dir = Path(lane_root) / REQUESTS
    requests_dir.mkdir(parents=True, exist_ok=True)
    for request in derived["requests"]:
        atomic_json(requests_dir / f"{request['id']}.json", request)
    if derived["requests"]:
        report.update(process_drafts(lane_root, repository, derived["requests"], items=items, reserved=reserved,
                                     head=head, changed_since=changed_since, max_level=max_level, source=source))

    def record(store: dict[str, Any]) -> None:
        store["checked"][repository] = {"at": _now(), "head": head}
        recent = store["ledger"][-100:]
        for ident in report["requests"]:
            if not any(e["kind"] == "GENERATED" and e["id"] == ident and e["detail"].get("head") == head for e in recent):
                _log(store, "GENERATED", repository, ident, {"head": head})
        for ident in report.get("local_lookahead", []):
            if not any(e["kind"] == "LOCAL_LOOKAHEAD" and e["id"] == ident and e["detail"].get("head") == head for e in recent):
                _log(store, "LOCAL_LOOKAHEAD", repository, ident,
                     {"head": head, "reason": "the head of the line needs a stronger builder; this slice is offered to the local builder"})
        for builder in (CAPABLE, NONE):
            for ident in report["routed"][builder]:
                reason = next(r["route"]["reason"] for r in derived["requests"] if r["id"] == ident)
                if not any(e["kind"] == "CAPABILITY_BLOCKER" and e["id"] == ident and e["detail"].get("head") == head
                           and e["detail"].get("reason") == reason for e in recent):
                    _log(store, "CAPABILITY_BLOCKER", repository, ident,
                         {"head": head, "builder_needed": builder, "reason": reason,
                          "route": "a capable, separately authorized builder" if builder == CAPABLE
                          else "none available for this repository yet"})
        for row in report["direction_required"]:
            key = json.dumps(row, sort_keys=True)
            if not any(e["kind"] == "DIRECTION_REQUIRED" and e["repository"] == repository
                       and json.dumps(e["detail"], sort_keys=True) == key for e in recent):
                _log(store, "DIRECTION_REQUIRED", repository, str(row.get("capability") or repository), row)

    _mutate(lane_root, record)
    return report


def log_event(lane_root: Path, kind: str, repository: str, ident: str, detail: Any) -> None:
    """Append one outcome (for example a capable-builder dispatch) to the same bounded ledger."""
    _mutate(lane_root, lambda store: _log(store, kind, repository, ident, detail))


def due(lane_root: Path, repository: str, *, now: datetime | None = None) -> bool:
    store = load_store(lane_root)
    prefix = id_prefix(repository)
    directory = Path(lane_root) / DRAFTS
    for draft in directory.glob("*.json") if directory.is_dir() else []:
        if draft.stem.startswith(prefix) and store["processed"].get(draft.stem) != hashlib.sha256(draft.read_bytes()).hexdigest():
            return True  # an unjudged draft is admitted promptly rather than after the interval
    row = store["checked"].get(repository)
    if not isinstance(row, dict):
        return True
    try:
        elapsed = ((now or datetime.now(UTC)) - datetime.fromisoformat(str(row["at"]))).total_seconds()
    except (KeyError, ValueError):
        return True
    return elapsed >= CHECK_INTERVAL_SECONDS


BLOCKING_EVENTS = {"CAPABLE_NOT_DISPATCHED", "CAPABILITY_BLOCKER", "PROGRESS_BLOCKED", "DIRECTION_REQUIRED", "LOCAL_DRAFT_FAILED"}


def request_status(lane_root: Path) -> list[dict[str, Any]]:
    """One row per open roadmap request: who must build it, and the latest recorded reason it is not being built.

    Read-only. The request files hold the routing decision; the bounded ledger holds the latest outcome per request, so
    the lane's idle report can say "waiting for the capable builder's daily budget" instead of only "idle".
    """
    root = Path(lane_root) / REQUESTS
    store = load_store(lane_root)
    latest: dict[str, dict[str, Any]] = {}
    for event in store["ledger"]:
        if isinstance(event, dict) and event.get("kind") in BLOCKING_EVENTS | {"CAPABLE_DISPATCHED", "ADMITTED", "GENERATED"}:
            latest[str(event.get("id"))] = event
    rows = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(request, dict):
            continue
        route = request.get("route") if isinstance(request.get("route"), dict) else {}
        event = latest.get(str(request.get("id")), {})
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        checked = store["checked"].get(str(request.get("repository")))
        checked_head = checked.get("head") if isinstance(checked, dict) else None
        rows.append({"id": request.get("id"), "repository": request.get("repository"),
                     "exact_head": str(request.get("exact_head", ""))[:12],
                     # False: the repository has moved on since this request was authorized; it is re-derived, not built
                     "head_is_current": None if checked_head is None else checked_head == request.get("exact_head"),
                     "builder": route.get("builder"),
                     "route_reason": route.get("reason"), "last_event": event.get("kind"),
                     "blocked_by": detail.get("reason") if event.get("kind") in BLOCKING_EVENTS else None})
    return rows


def summarize(report: dict[str, Any]) -> str:
    return (f"requests {len(report['requests'])}; admitted {len(report['admitted'])}; rejected {len(report['rejected'])}; "
            f"capability blockers {len(report['blocked'])}; direction required {len(report['direction_required'])}; "
            f"skipped {len(report['skipped'])}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Show roadmap-refill requests, admitted items and the ledger.")
    parser.add_argument("lane_root", type=Path)
    args = parser.parse_args(argv)
    store = load_store(args.lane_root)
    directory = Path(args.lane_root) / REQUESTS
    requests = sorted(directory.glob("*.json")) if directory.is_dir() else []
    print(json.dumps({"requests": [p.stem for p in requests], "admitted": [i["id"] for i in store["items"]],
                      "ledger": store["ledger"][-20:]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
