"""Read-only BLACKICE GitHub release lineage and installer digest probe.

This establishes whether a *published* release actually contains the
upstream HTTPS-10000 change (PR #24). It does not install/run an executable,
contact a user's MCP endpoint, grant a connector tool or approve cutover.
Public GitHub metadata and optional local SHA-256 are evidence, not a security
review or independent proof of an authenticated ChatGPT Plus invocation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = "MoonlightClarity/BLACKICE"
REQUIRED_PR = 24
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TAG = re.compile(r"^v[0-9]+(?:\.[0-9]+){2}$")
_INSTALLER = re.compile(r"^Blackice-[0-9]+(?:\.[0-9]+){2}-Setup\.exe$", re.IGNORECASE)
MAX_API_BYTES = 1_000_000


class ProbeError(ValueError):
    """Missing, malformed or unverified public release evidence."""


def github_get(path: str) -> dict[str, Any]:
    """Run only the GitHub CLI's read-only HTTP GET for a fixed public repo."""
    prefix = re.escape(f"repos/{REPO}/")
    allowable = (
        prefix + r"releases/latest",
        prefix + r"releases/tags/v[0-9]+(?:\.[0-9]+){2}",
        prefix + r"git/ref/tags/v[0-9]+(?:\.[0-9]+){2}",
        prefix + r"git/tags/[0-9a-f]{40}",
        prefix + r"pulls/24",
        prefix + r"compare/[0-9a-f]{40}\.\.\.[0-9a-f]{40}",
    )
    if not any(re.fullmatch(pattern, path) for pattern in allowable):
        raise ProbeError("UNSAFE_GITHUB_ENDPOINT")
    try:
        p = subprocess.run(
            ["gh", "api", "--method", "GET", path],
            check=False, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("GITHUB_EVIDENCE_UNAVAILABLE") from exc
    if p.returncode or not 0 < len(p.stdout) <= MAX_API_BYTES:
        raise ProbeError("GITHUB_EVIDENCE_UNAVAILABLE")
    try:
        value = json.loads(p.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ProbeError("GITHUB_EVIDENCE_MALFORMED") from exc
    if not isinstance(value, dict):
        raise ProbeError("GITHUB_EVIDENCE_MALFORMED")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ProbeError("EXACT_COMMIT_SHA_REQUIRED")
    return value


def _release_tag(tag: object) -> str:
    if not isinstance(tag, str) or _TAG.fullmatch(tag) is None:
        raise ProbeError("INVALID_RELEASE_TAG")
    return tag


def _tag_commit(api: Callable[[str], dict[str, Any]], tag: str) -> str:
    target = api(f"repos/{REPO}/git/ref/tags/{tag}").get("object")
    for _ in range(3):
        if not isinstance(target, dict):
            raise ProbeError("INVALID_RELEASE_TAG_TARGET")
        target_sha = _sha(target.get("sha"))
        if target.get("type") == "commit":
            return target_sha
        if target.get("type") != "tag":
            raise ProbeError("INVALID_RELEASE_TAG_TARGET")
        target = api(f"repos/{REPO}/git/tags/{target_sha}").get("object")
    raise ProbeError("RELEASE_TAG_INDIRECTION_TOO_DEEP")


def _installer_asset(release: dict[str, Any]) -> dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ProbeError("RELEASE_ASSETS_UNAVAILABLE")
    candidates = [
        a for a in assets if isinstance(a, dict)
        and isinstance(a.get("name"), str) and _INSTALLER.fullmatch(a["name"])
    ]
    if len(candidates) != 1:
        raise ProbeError("UNIQUE_WINDOWS_INSTALLER_REQUIRED")
    name, digest = candidates[0]["name"], candidates[0].get("digest")
    if name.lower() != f"blackice-{release['tag_name'][1:]}-setup.exe".lower():
        raise ProbeError("INSTALLER_VERSION_TAG_MISMATCH")
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise ProbeError("PUBLISHED_INSTALLER_SHA256_UNAVAILABLE")
    size = candidates[0].get("size")
    if type(size) is not int or not 1 <= size <= 1_000_000_000:
        raise ProbeError("INSTALLER_SIZE_INVALID")
    return {"name": name, "sha256": digest.split(":", 1)[1], "size": size}


def _local_installer_hash(path: Path, expected_size: int) -> str:
    """Hash bytes only; NEVER launch an installer or invoke Win32 APIs on it."""
    if not path.is_file() or path.is_symlink():
        raise ProbeError("LOCAL_INSTALLER_NOT_REGULAR_FILE")
    if path.stat().st_size != expected_size:
        raise ProbeError("LOCAL_INSTALLER_SIZE_MISMATCH")
    checksum = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def inspect_release(
    *, api: Callable[[str], dict[str, Any]] = github_get,
    tag: str | None = None, local_installer: Path | None = None,
) -> dict[str, Any]:
    """Inspect source ancestry and optional downloaded bytes, never deploy.

    A positive result says only that source ancestry and package bytes match
    GitHub's observed release metadata; it cannot attest OS trust/signature,
    a ChatGPT Plus tool invocation, DoWork scheduler or revocable host access.
    """
    if tag is not None:
        _release_tag(tag)
    route = f"repos/{REPO}/releases/tags/{tag}" if tag else f"repos/{REPO}/releases/latest"
    release = api(route)
    actual_tag = _release_tag(release.get("tag_name"))
    if tag is not None and tag != actual_tag:
        raise ProbeError("RELEASE_TAG_MISMATCH")
    release_head = _tag_commit(api, actual_tag)
    pr = api(f"repos/{REPO}/pulls/{REQUIRED_PR}")
    merge_head = _sha(pr.get("merge_commit_sha"))
    if pr.get("merged_at") is None or pr.get("number") != REQUIRED_PR:
        raise ProbeError("REQUIRED_UPSTREAM_PR_NOT_MERGED")
    comparison = api(f"repos/{REPO}/compare/{merge_head}...{release_head}")
    relationship = comparison.get("status")
    if relationship not in {"ahead", "identical", "behind", "diverged"}:
        raise ProbeError("RELEASE_LINEAGE_UNVERIFIED")
    contains_fix = relationship in {"ahead", "identical"}
    asset = _installer_asset(release)
    checked_digest: bool | None = None
    if local_installer is not None:
        checked_digest = _local_installer_hash(Path(local_installer), asset["size"]) == asset["sha256"]
    source_gate = "INCLUDES_REQUIRED_PR" if contains_fix else "RELEASE_PRECEDES_OR_DIVERGES_FROM_REQUIRED_PR"
    integrity_gate = ("SHA256_MATCHES_GITHUB" if checked_digest is True else
                      "SHA256_MISMATCH" if checked_digest is False else "NOT_CHECKED")
    return {
        "kind": "BLACKICE_GITHUB_RELEASE_SOURCE_PROBE_V1",
        "release_tag": actual_tag,
        "release_commit": release_head,
        "published_at": release.get("published_at"),
        "required_pr": REQUIRED_PR,
        "required_pr_merge_commit": merge_head,
        "compare_status": relationship,
        "source_gate": source_gate,
        "installer_name": asset["name"],
        "installer_sha256": asset["sha256"],
        "installer_bytes_checked": checked_digest is not None,
        "installer_integrity_gate": integrity_gate,
        "unverified_independent_gates": [
            "WINDOWS_AUTHENTICODE_AND_MALWARE_POLICY",
            "INSTALLED_BUILD_IDENTITY",
            "PLUS_HOSTED_CHAT_MCP_TOOL_INVOCATION",
            "SCHEDULED_DOWORK_MCP_TOOL_INVOCATION",
            "OWNER_AND_WORKER_LEASE_INDEPENDENCE",
            "TWO_RESTART_RECOVERY_AND_ROLLBACK",
        ],
        "ready_for_laptop_install": False,
        "ready_to_cancel_paid_remote": False,
        "local_mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Exact vMAJOR.MINOR.PATCH GitHub release tag")
    parser.add_argument("--local-installer", type=Path, help="Optional installer to hash, never execute")
    args = parser.parse_args(argv)
    try:
        report = inspect_release(tag=args.tag, local_installer=args.local_installer)
    except ProbeError as exc:
        print(json.dumps({
            "kind": "BLACKICE_GITHUB_RELEASE_SOURCE_PROBE_V1", "status": "UNVERIFIED",
            "error": str(exc), "ready_for_laptop_install": False,
            "ready_to_cancel_paid_remote": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
