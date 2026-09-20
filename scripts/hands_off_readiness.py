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


def _repository_id(repo: Path) -> str:
    """Return stable owner/name identity from origin instead of a machine-local path."""
    remote = _git(repo, "remote", "get-url", "origin").strip()
    value = remote.removesuffix(".git")
    if value.startswith("git@github.com:"):
        value = value.split(":", 1)[1]
    elif "github.com/" in value:
        value = value.split("github.com/", 1)[1]
    value = value.strip("/")
    if value.count("/") != 1:
        raise RuntimeError("origin is not a canonical GitHub owner/name repository")
    return value

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


def alignment_sufficiency(repo: Path, tracked: set[str]) -> dict[str, Any]:
    """Require durable product intent before autonomous mutation."""
    groups = {
        "purpose": ("README.md", "docs/VISION.md", "VISION.md"),
        "roadmap": ("docs/ROADMAP.md", "ROADMAP.md"),
    }
    missing: list[str] = []
    evidence: dict[str, list[str]] = {}
    for name, candidates in groups.items():
        hits = [path for path in candidates if path in tracked]
        evidence[name] = hits
        if not hits:
            missing.append(name)
    status = "ALIGNED" if not missing else "ALIGNMENT_INSUFFICIENT"
    return {
        "status": status,
        "evidence": evidence,
        "missing": missing,
        "request": None if not missing else {
            "type": "DIRECTION_REQUIRED",
            "message": "More project direction is required before autonomous development can safely choose work.",
            "needed": missing,
        },
    }


def activation_allowed(report: dict[str, Any]) -> bool:
    """Return True only for an exact, complete PASS result."""
    repositories = report.get("repositories")
    return bool(
        report.get("status") == "PASS"
        and isinstance(repositories, list)
        and repositories
        and all(r.get("status") == "PASS" and r.get("head") for r in repositories)
    )


def audit(repo: Path, contract: dict[str, Any]) -> dict[str, Any]:
    repo = repo.resolve()
    result: dict[str, Any] = {
        "repository": None,
        "local_path": str(repo),
        "head": None,
        "status": "UNKNOWN",
        "checks": [],
    }
    try:
        result["repository"] = _repository_id(repo)
        result["head"] = _git(repo, "rev-parse", "HEAD")
        tracked = _tracked_files(repo)
        corpus = _text_corpus(repo, tracked)
        result["alignment"] = alignment_sufficiency(repo, tracked)
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
        if requirement.get("id") == "ci":
            workflow_evidence = sorted(path for path in tracked
                if path.startswith(".github/workflows/") and path.lower().endswith((".yml", ".yaml")))
            for path in workflow_evidence:
                if path not in evidence:
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

    authority_status = (
        "PASS" if statuses and all(s == "PASS" for s in statuses)
        else "UNKNOWN" if not statuses or any(s == "UNKNOWN" for s in statuses)
        else "FAIL"
    )
    result["authority_status"] = authority_status
    result["status"] = (
        "PASS" if authority_status == "PASS" and result["alignment"]["status"] == "ALIGNED"
        else "ALIGNMENT_INSUFFICIENT" if authority_status == "PASS"
        else authority_status
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
    parser.add_argument("--product", action="append", dest="selected_products",
                        help="Audit only this declared product; may be repeated.")
    parser.add_argument(
        "--activation-state",
        type=Path,
        help="Write a fail-closed activation decision bound to audited exact heads.",
    )
    args = parser.parse_args()

    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "UNKNOWN", "error": f"contract unavailable: {exc}"}))
        return 2

    declared_products = contract.get("products", {})
    selected = set(args.selected_products or declared_products)
    unknown = selected - set(declared_products)
    if unknown:
        print(json.dumps({"status":"UNKNOWN","error":f"unknown product(s): {sorted(unknown)}"}))
        return 2
    selected_repo_ids = {repo_id for name in selected for repo_id in declared_products[name]}
    audited = [audit(repo, contract) for repo in args.repositories]
    report = {
        "schema_version": 2,
        "contract": str(args.contract),
        "repositories": [r for r in audited if r.get("repository") in selected_repo_ids],
    }
    by_repo = {r.get("repository"): r for r in report["repositories"] if r.get("repository")}
    products = {}
    for product, repository_ids in declared_products.items():
        if product not in selected:
            continue
        members = [by_repo.get(repo_id) for repo_id in repository_ids]
        if any(member is None for member in members):
            status = "UNKNOWN"
        elif all(member.get("status") == "PASS" for member in members):
            status = "PASS"
        elif any(member.get("status") == "UNKNOWN" for member in members):
            status = "UNKNOWN"
        else:
            status = "FAIL"
        products[product] = {
            "status": status,
            "repositories": {
                repo_id: (by_repo.get(repo_id) or {}).get("head")
                for repo_id in repository_ids
            },
        }
    report["products"] = products
    report["status"] = (
        "PASS" if report["repositories"] and all(r["status"] == "PASS" for r in report["repositories"])
        else "UNKNOWN" if not report["repositories"] or any(r["status"] == "UNKNOWN" for r in report["repositories"])
        else "FAIL"
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.activation_state:
        activation = {
            "schema_version": 2,
            "enabled": any(p["status"] == "PASS" for p in report.get("products", {}).values()),
            "reason": "READINESS_PASS" if any(p["status"] == "PASS" for p in report.get("products", {}).values()) else "READINESS_NOT_PASS",
            "heads": {
                r["repository"]: r["head"]
                for r in report["repositories"]
                if r.get("repository") and r.get("head")
            },
            "products": {
                name: {
                    "enabled": product["status"] == "PASS",
                    "status": product["status"],
                    "repositories": product["repositories"],
                }
                for name, product in report.get("products", {}).items()
            },
        }
        args.activation_state.parent.mkdir(parents=True, exist_ok=True)
        args.activation_state.write_text(
            json.dumps(activation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(rendered, end="")
    # Runtime readiness is product-scoped: a failing independent product must not
    # suppress valid activation evidence for another product.
    return 0 if any(p["status"] == "PASS" for p in products.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
