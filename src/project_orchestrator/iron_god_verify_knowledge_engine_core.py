"""Real verify_and_commit substitute for Knowledge Engine Core
(2026-10-18 live commissioning).

local_continuous_worker.py's own verify_and_commit hard-codes a call
to `engineering/preflight.py` (project-orchestrator's own 26-named-
check preflight script) and validates its evidence against that exact
schema (head_sha, mode=="FULL", status=="GREEN", exactly 26 uniquely
named all-passing results). Knowledge Engine Core has neither that
file nor that schema -- its own documented verification procedure
(CONTRIBUTING.md) is `poetry run python scripts/quality_preflight.py`,
a different script entirely.

execute_candidate_attempt (local_continuous_worker.py) already
supports substituting verify_and_commit via a module-global lookup
(`verifier_fn = verify_and_commit if verifier_fn is None else
verifier_fn`) -- the SAME dependency-injection seam this session has
used for every builder/verifier substitute all along (documented as
VerifierFn in iron_god_orchestration_caller.py). Using it for a real,
repository-specific verifier requires no edit to
local_continuous_worker.py at all: a caller installs this function
onto the loaded worker module's `verify_and_commit` attribute before
dispatching.

Confirmed directly by reading execute_candidate_attempt: when
verifier_fn is not identically the module-global verify_and_commit
object, it is called as `verifier_fn(task)`, WITHOUT lease_heartbeat --
so this function accepts but does not rely on lease_heartbeat being
called. The real Knowledge Engine Core test suite (confirmed by an
actual run this session) completes in ~47 seconds; the slice lease's
own TTL is 1800 seconds, freshly renewed moments earlier when the
build phase ended -- comfortably inside that window without
heartbeat renewal for this repository's actual suite size. A much
larger test suite would need this reconsidered.

execute_candidate_attempt only ever reads three keys from a
verifier's return value (head_sha, receipt_sha256, checks) -- this
function's internal evidence shape is otherwise its own business, and
is deliberately NOT shaped like project-orchestrator's own 26-check
schema, since claiming that shape for a different repository's
different real checks would be exactly the kind of fabricated
evidence this project's own fail-closed culture refuses to produce.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

KNOWLEDGE_ENGINE_CORE_VENV_PYTHON = Path(
    r"C:\Users\jerem\Documents\GitHub\knowledge-engine-core\.venv\Scripts\python.exe"
)


class KnowledgeEngineCoreVerificationError(RuntimeError):
    """Raised using the same failure-code vocabulary as the real
    verify_and_commit where a shared concept applies
    (EXPECTED_SOURCE_OR_TEST_MISSING, STAGED_PATH_MISMATCH), so this
    substitute stays compatible with execute_candidate_attempt's own
    repairable-failure-code conversion logic for those two codes."""


def _git(work: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(work), *args],
        capture_output=True,
        text=True,
    )


def _run_check(work: Path, cmd: list[str], *, name: str, timeout: int) -> None:
    try:
        result = subprocess.run(
            cmd, cwd=work, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise KnowledgeEngineCoreVerificationError(
            f"KNOWLEDGE_ENGINE_CORE_CHECK_TIMEOUT:{name}"
        ) from exc
    if result.returncode != 0:
        tail = (result.stdout[-3000:] + result.stderr[-3000:]).strip()
        raise KnowledgeEngineCoreVerificationError(
            f"KNOWLEDGE_ENGINE_CORE_CHECK_FAILED:{name}:{tail}"
        )


def verify_and_commit_knowledge_engine_core(
    task: dict, *, lease_heartbeat=None
) -> dict:
    """Real verification for a Knowledge Engine Core candidate slice:
    real ruff format --check, ruff check, mypy, focused pytest, and the
    full pytest suite (the repository's own CONTRIBUTING.md standard),
    then a real git commit of exactly the declared source+tests paths.

    Raises KnowledgeEngineCoreVerificationError (a RuntimeError) on any
    failure -- EXPECTED_SOURCE_OR_TEST_MISSING and STAGED_PATH_MISMATCH
    specifically, matching codes execute_candidate_attempt already
    knows how to convert into a repairable StageFailure; any other
    check failure is reported under its own
    KNOWLEDGE_ENGINE_CORE_CHECK_FAILED:<name> code (not one of the
    three codes execute_candidate_attempt treats as repairable, so it
    propagates bare -- an unrecognized Knowledge Engine Core-specific
    failure is never guessed into "this can be auto-repaired").
    """
    ident = task["id"]
    source = task["source"]
    tests = task["tests"]
    work = task["work"]
    python = str(KNOWLEDGE_ENGINE_CORE_VENV_PYTHON)

    if not (work / source).is_file() or not (work / tests).is_file():
        raise KnowledgeEngineCoreVerificationError(
            "EXPECTED_SOURCE_OR_TEST_MISSING:" + ident
        )

    checks_run: list[str] = []

    _run_check(
        work,
        [python, "-m", "ruff", "format", "--check", source, tests],
        name="ruff_format",
        timeout=90,
    )
    checks_run.append("ruff_format")

    _run_check(
        work,
        [python, "-m", "ruff", "check", source, tests],
        name="ruff_check",
        timeout=90,
    )
    checks_run.append("ruff_check")

    _run_check(work, [python, "-m", "mypy", source], name="mypy", timeout=180)
    checks_run.append("mypy")

    _run_check(
        work,
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", tests],
        name="pytest_focused",
        timeout=600,
    )
    checks_run.append("pytest_focused")

    # The handoff's own acceptance criteria require the FULL suite to
    # pass, matching this repository's own CONTRIBUTING.md standard --
    # a focused-only pass could hide a regression elsewhere.
    _run_check(
        work,
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        name="pytest_full",
        timeout=900,
    )
    checks_run.append("pytest_full")

    add = _git(work, "add", "--", source, tests)
    if add.returncode != 0:
        raise KnowledgeEngineCoreVerificationError("GIT_ADD_FAILED:" + ident)
    staged = _git(work, "diff", "--cached", "--name-only")
    names = {row.strip() for row in staged.stdout.splitlines() if row.strip()}
    if names != {source, tests}:
        raise KnowledgeEngineCoreVerificationError(
            "STAGED_PATH_MISMATCH:" + repr(names)
        )

    commit = _git(
        work, "commit", "-m", "feat(iron-god): " + ident + " local verified slice"
    )
    if commit.returncode != 0:
        raise KnowledgeEngineCoreVerificationError("GIT_COMMIT_FAILED:" + ident)
    head = _git(work, "rev-parse", "HEAD").stdout.strip()

    evidence = {
        "head_sha": head,
        "repository": "jweter/knowledge-engine-core",
        "checks_run": checks_run,
        "procedure": (
            "real ruff format --check / ruff check / mypy / pytest "
            "(focused + full), per CONTRIBUTING.md"
        ),
    }
    receipt_sha256 = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "head_sha": head,
        "receipt_sha256": receipt_sha256,
        "checks": len(checks_run),
    }
