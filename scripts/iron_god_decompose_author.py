"""Propose a Level 1 decomposition of one roadmap slice with the LOCAL model, in its own process.

Spawned by ``iron_god_task_decomposer.advance`` (one job at a time, holding the shared local-model lease). It reads the
slice request, builds a short repository brief from the exact base commit, asks the local Ollama model for a plan
(at most two attempts, the second one told why the first was rejected), and writes the raw proposal. The lane
re-validates it deterministically before anything is accepted, so a weak model costs minutes, never a bad plan.

Bound to the owner's execution authority like every other worker: if the owner has taken control it stops and says so
(``EXECUTION_AUTHORITY_REVOKED``), which never spends an attempt. No network other than the local Ollama endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BRIEF_FILES = 40


def brief(clone: Path, base: str, allowed: list[str]) -> str:
    """Existing python files under the allowed roots (so the model puts NEW modules next to them and does not reuse one)."""
    roots = sorted(
        {
            p.split("*", 1)[0].rstrip("/") or "."
            for p in allowed
            if not p.endswith((".md", ".json"))
        }
    )
    listing = subprocess.run(
        ["git", "-C", str(clone), "ls-tree", "-r", "--name-only", base, *roots],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    ).stdout.split()
    files = [f for f in listing if f.endswith(".py") and "__pycache__" not in f][
        :BRIEF_FILES
    ]
    return "existing python files: " + (
        ", ".join(files) if files else "(none under the allowed roots)"
    )


def ask_ollama(prompt: str, model: str, timeout: int = 1500) -> Any:
    base = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_ctx": 6144, "num_predict": 1800},
    }
    request = urllib.request.Request(
        base + "/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(json.load(response).get("response") or "{}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    for name in ("request", "clone", "base", "result"):
        parser.add_argument(
            f"--{name}", required=True, type=Path if name != "base" else str
        )
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    from project_orchestrator import iron_god_task_decomposer as decomposer
    from project_orchestrator.execution_authority import evaluate_execution_authority
    from scripts.autonomous_slice_builder import MODEL

    request = json.loads(args.request.read_text(encoding="utf-8"))
    model = MODEL.split("/", 1)[-1]
    args.result.parent.mkdir(parents=True, exist_ok=True)

    def finish(payload: dict[str, Any]) -> int:
        tmp = args.result.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, args.result)
        return 0

    try:
        permitted = (
            evaluate_execution_authority(
                release_sha=os.environ.get("IRON_GOD_ACCEPTED_SHA")
            ).permitted
            is True
        )
    except Exception:
        permitted = False
    if not permitted:
        return finish(
            {"ok": False, "reason": "RuntimeError: EXECUTION_AUTHORITY_REVOKED"}
        )
    base = str(args.base)
    result = decomposer.propose_staged(
        request,
        brief(args.clone, base, request["allowed_paths"]),
        lambda p: ask_ollama(p, model),
        exists=decomposer._exists_at(args.clone, base),
        read=decomposer._read_at(args.clone, base),
    )
    return finish({**result, "model": model, "base": base})


if __name__ == "__main__":
    raise SystemExit(main())
