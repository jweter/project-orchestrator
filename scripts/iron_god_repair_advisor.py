"""One-shot, owner-authorized *advisory* Ollama repair outline from a lane checkpoint.

Manual/authorized invocation only. This is not installed as a supervisor job,
and it cannot admit or execute any generated microtask.

Example (only after an existing permitted owner session):
    python scripts/iron_god_repair_advisor.py --lane <lane> --clone <repo-clone> \
      --parent <parent> --repository jweter/project-orchestrator \
      --head <40-hex-sha> --failed-key t1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Owner-gated local-only microtask repair advice"
    )
    parser.add_argument("--lane", type=Path, required=True)
    parser.add_argument("--clone", type=Path, required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--failed-key", required=True)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from project_orchestrator.execution_authority import evaluate_execution_authority
    from project_orchestrator.iron_god_local_model import (
        BUILDER_MODEL,
        guarded_generate,
    )
    from project_orchestrator.iron_god_microtask_repair_advisor import propose_current

    # No command-line option or mailbox message can provide a grant. This
    # requires the owner's existing, supervisor-acknowledged release authority.
    release_sha = os.environ.get("IRON_GOD_ACCEPTED_SHA")
    provenance: dict[str, Any] = {
        "provider": "LOCAL_OLLAMA_ONLY",
        "requested": BUILDER_MODEL,
    }

    def authorized() -> bool:
        if evaluate_execution_authority(release_sha=release_sha).permitted is not True:
            return False
        # A caller-supplied SHA and old advisory checkpoint cannot establish
        # current repository truth. Recheck local exact HEAD before/after model
        # work; no remote GitHub calls or source mutations are made.
        try:
            observed = subprocess.run(
                ["git", "-C", str(args.clone), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return False
        return observed == args.head

    def ask(prompt: str) -> Any:
        reply = guarded_generate(
            prompt,
            requested=BUILDER_MODEL,
            num_ctx=6144,
            num_predict=1700,
            json_mode=True,
            timeout=900,
            wait_s=60,
        )
        provenance["model_used"] = reply.get("model_used")
        provenance["lowest_available_gib"] = reply.get("lowest_available_gib")
        return json.loads(reply.get("response") or "{}")

    result = propose_current(
        args.lane,
        args.parent,
        args.repository,
        args.head,
        args.failed_key,
        authority=authorized,
        ask_local=ask,
        model_provenance=provenance,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "path": result.get("path"),
                "reason": result.get("reason"),
                "advisory_only": True,
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] in {"ADVISORY_RECORDED", "ALREADY_RECORDED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
