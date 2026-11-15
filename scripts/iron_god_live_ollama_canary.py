"""Explicit, owner-gated, local Ollama three-edit *disposable* codegen canary.

No auto-start, no cloud fallback, no writes into real product repos, no
release-pin or controller mutation. The native tests and Git/SQLite acceptance
use the same deliberately disposable fixture exercised by #722.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
_SHA = re.compile(r"[a-f0-9]{40}\Z")


class CanaryRefusal(RuntimeError):
    """No generated code was admitted or source was accepted by this canary."""


def run_disposable(
    *,
    expected_release_sha: str,
    actual_release_sha: str,
    authorized: Callable[[], bool],
    ask_local: Callable[[str], Any],
) -> dict[str, Any]:
    """Injectable adapter: in actual CLI, ask_local is guarded Ollama only."""
    if not __debug__:
        return {"status": "OPTIMIZED_PYTHON_UNSAFE_FOR_CANARY", "verified_edits": 0}
    for field in (expected_release_sha, actual_release_sha):
        if not isinstance(field, str) or not _SHA.fullmatch(field):
            return {"status": "RELEASE_IDENTITY_UNVERIFIED", "verified_edits": 0}
    if expected_release_sha != actual_release_sha:
        return {"status": "RELEASE_HEAD_MISMATCH", "verified_edits": 0}
    if not authorized():
        return {"status": "OWNER_AUTHORITY_DENIED", "verified_edits": 0}

    from project_orchestrator.iron_god_canary_codegen import propose_source
    from tests.test_iron_god_three_microedit_resume_canary import (
        run_disposable_three_edits,
    )

    source_hashes: list[str] = []
    call_count = [0]

    def counted_model(prompt: str) -> Any:
        call_count[0] += 1
        return ask_local(prompt)

    def build(step: int) -> str:
        if not authorized():
            raise CanaryRefusal("OWNER_AUTHORITY_DENIED")
        result = propose_source(step, counted_model, authorized)
        if result["status"] != "LOCAL_SOURCE_VALIDATED":
            raise CanaryRefusal(result["status"])
        source_hashes.append(result["sha256"])
        return result["source"]

    try:
        with tempfile.TemporaryDirectory(prefix="ig710-local-ollama-canary-") as folder:
            proof = run_disposable_three_edits(
                Path(folder),
                source_builder=build,
                external_authorized=authorized,
            )
    except (CanaryRefusal, AssertionError, OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "LOCAL_DISPOSABLE_CANARY_BLOCKED_OR_FAILED",
            "error_type": type(exc).__name__,
            "reason": str(exc)[-380:],
            "verified_edits": 0,  # no complete end-to-end acceptance proof
            "local_model_requests": call_count[0],
            "validated_source_hashes": source_hashes,
        }

    if not authorized():
        return {
            "status": "OWNER_AUTHORITY_REVOKED_POST_RUN",
            "verified_edits": 0,
            "local_model_requests": call_count[0],
        }
    commits = proof.get("verified_commits") or []
    if (
        not proof.get("native_tests_green_after_each")
        or not proof.get("pause_after_second")
        or not proof.get("resumed_without_duplicate")
        or len(commits) != 3
        or len(set(commits)) != 3
        or len(source_hashes) != 3
    ):
        return {"status": "DISPOSABLE_EVIDENCE_INCOMPLETE", "verified_edits": 0}
    return {
        "status": "THREE_INJECTED_LOCAL_MICROEDITS_VERIFIED",
        "verified_edits": 3,
        "local_model_requests": call_count[0],
        "validated_source_hashes": source_hashes,
        "disposable_commit_shas": commits,
        "native_test_verified": True,
        "owner_pause_after_second": True,
        "durable_restart_resume": True,
    }


def _safe_receipt(
    result: dict[str, Any], *, actual_local_model: bool
) -> dict[str, Any]:
    """Do not conflate an injected fake with a genuine guarded local model."""
    return {
        **result,
        "engine_started": False,
        "installed_release_changed": False,
        "production_repository_changed": False,
        "local_ollama_requested": actual_local_model,
        "live_ollama_proven": bool(
            actual_local_model
            and result.get("status") == "THREE_INJECTED_LOCAL_MICROEDITS_VERIFIED"
        ),
        "zero_cloud_token_use_proven": False,
        "product_reality_proven": False,
        "deployment_ready": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Owner-authorized 3-edit guarded Ollama disposable test; never starts engine."
    )
    parser.add_argument(
        "--run-disposable-live",
        action="store_true",
        help="Explicit manual invocation; requires accepted release and supervisor-acknowledged owner grant.",
    )
    args = parser.parse_args(argv)
    # This command reuses the disposable Git/native-test fixture, whose
    # historical assertions are disabled by -O/PYTHONOPTIMIZE. Refuse *before*
    # model admission, even when a caller explicitly requests a live trial.
    if sys.flags.optimize != 0:
        print(
            json.dumps(
                _safe_receipt(
                    {
                        "status": "OPTIMIZED_PYTHON_UNSAFE_FOR_CANARY",
                        "verified_edits": 0,
                    },
                    actual_local_model=False,
                ),
                sort_keys=True,
            )
        )
        return 2
    if not args.run_disposable_live:
        print(
            json.dumps(
                _safe_receipt(
                    {
                        "status": "EXPLICIT_LIVE_CANARY_REQUEST_REQUIRED",
                        "verified_edits": 0,
                    },
                    actual_local_model=False,
                ),
                sort_keys=True,
            )
        )
        return 2

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))
    from project_orchestrator.execution_authority import evaluate_execution_authority
    from project_orchestrator.iron_god_local_model import (
        BUILDER_MODEL,
        guarded_generate,
    )

    accepted = os.environ.get("IRON_GOD_ACCEPTED_SHA", "")
    if not _SHA.fullmatch(accepted):
        receipt = _safe_receipt(
            {"status": "ACCEPTED_RELEASE_NOT_IDENTIFIED", "verified_edits": 0},
            actual_local_model=False,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 2

    def authorized() -> bool:
        if evaluate_execution_authority(release_sha=accepted).permitted is not True:
            return False
        try:
            repo_head = subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return False
        return repo_head == accepted

    try:
        current_head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        current_head = ""

    seen_models: list[str] = []

    def call_local(prompt: str) -> Any:
        reply = guarded_generate(
            prompt,
            requested=BUILDER_MODEL,
            num_ctx=2048,
            num_predict=200,
            json_mode=True,
            temperature=0.0,
            timeout=240,
            wait_s=45,
        )
        seen_models.append(str(reply.get("model_used") or "UNVERIFIED"))
        return json.loads(reply.get("response") or "{}")

    result = run_disposable(
        expected_release_sha=accepted,
        actual_release_sha=current_head,
        authorized=authorized,
        ask_local=call_local,
    )
    receipt = _safe_receipt(
        result,
        actual_local_model=bool(
            seen_models and all(model == BUILDER_MODEL for model in seen_models)
        ),
    )
    receipt["observed_models"] = seen_models
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["live_ollama_proven"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
