#!/usr/bin/env python3
"""Fail-closed Hands-Off Evolution authority-readiness auditor."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "git command failed")
    return proc.stdout.strip()


def _tracked_files(repo: Path) -> set[str]:
    return {line.replace("\\", "/") for line in _git(repo, "ls-files").splitlines() if line}


def _text_corpus(repo: Path, tracked: set[str]) -> str:
    allowed = {".md", ".txt", ".json", ".toml", ".yaml", ".yml"}
    chunks: list[str] = []
    for rel in sorted(tracked):
        path = repo / rel
        if path.suffix.lower() not in allowed:
            continue
        try:
            if path.stat().st_size <= 1_000_000:
                chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks).lower()


def audit(repo: Path, contract: dict[str, Any]) -> dict[str, Any]:
    repo = repo.resolve()
    result: dict[str, Any] = {
        "repository": str(repo),
        "head": None,
        "status": "UNKNOWN",
        "checks": [],
    }
    try:
        result["head"] = _git(repo, "rev-parse", "HEAD")
        tracked = _tracked_files(repo)
        corpus = _text_corpus(repo, tracked)
    except (OSError, RuntimeError) as exc:
        result["error"] = str(exc)
        return result

    statuses: list[str] = []
    for requirement in contract.get("required_authority", []):
        evidence: list[str] = []
        paths = requirement.get("evidence_any", [])
        for path in paths:
            if path in tracked:
                evidence.append(path)
        signals = [str(s).lower() for s in requirement.get("text_signal_any", [])]
        signal_hits = [s for s in signals if s in corpus]

        if paths and evidence:
            status = "PASS"
        elif signals and signal_hits:
            status = "PASS"
        elif paths or signals:
            status = "FAIL"
        else:
            status = "UNKNOWN"

        statuses.append(status)
        result["checks"].append(
            {
                "id": requirement.get("id", "unknown"),
                "status": status,
                "evidence": evidence,
                "signal_hits": signal_hits,
            }
        )

    result["status"] = (
        "PASS" if statuses and all(s == "PASS" for s in statuses)
        else "UNKNOWN" if not statuses or any(s == "UNKNOWN" for s in statuses)
        else "FAIL"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repositories", nargs="+", type=Path)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "engineering" / "hands-off-evolution.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "UNKNOWN", "error": f"contract unavailable: {exc}"}))
        return 2

    report = {
        "schema_version": 1,
        "contract": str(args.contract),
        "repositories": [audit(repo, contract) for repo in args.repositories],
    }
    report["status"] = (
        "PASS" if report["repositories"] and all(r["status"] == "PASS" for r in report["repositories"])
        else "UNKNOWN" if not report["repositories"] or any(r["status"] == "UNKNOWN" for r in report["repositories"])
        else "FAIL"
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
