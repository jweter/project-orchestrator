"""Protected-ref namespaces must not collide: Git cannot hold a ref and a ref directory of the same name (#597).

The product lane anchors each verified packet at ``refs/iron-god/accepted/<packet-id>``; ``LocalGitRefs`` keeps one
cumulative baseline at ``refs/iron-god/accepted``. They never share a repository today, which is the only reason
nothing breaks. See docs/IRON_GOD_597_INTEGRATION_PROPOSAL_2026-11-01.md section 2.1.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from project_orchestrator import iron_god_candidate_refs, iron_god_milestone_refs, local_git_refs

SRC = Path(local_git_refs.__file__).parent
ZERO = "0" * 40


def lane_anchor_template() -> str:
    """The lane's per-packet ref prefix, read from its source so a rename there is noticed."""
    text = (SRC / "iron_god_product_lane.py").read_text(encoding="utf-8")
    match = re.search(r'f"(refs/iron-god/[a-z_/-]+)/\{', text)
    assert match, "the lane's accepted-ref pattern was not found; update this test with the lane"
    return match.group(1)


def cumulative_baseline_ref() -> str:
    text = (SRC / "local_git_refs.py").read_text(encoding="utf-8")
    match = re.search(r'"(refs/iron-god/[a-z_/-]+)",\s*\n?\s*allowed=\(0, 1\)', text)
    assert match, "the cumulative baseline ref was not found; update this test with LocalGitRefs"
    return match.group(1)


def all_ref_names() -> dict[str, str]:
    names = {f"{lane_anchor_template()}/<packet-id>": "product lane anchor",
             cumulative_baseline_ref(): "LocalGitRefs cumulative baseline",
             f"{iron_god_candidate_refs.REF_PREFIX}/<slice-id>": "candidate anchor",
             f"{iron_god_milestone_refs.REF_PREFIX}/<milestone-id>": "milestone anchor"}
    for kind in sorted(local_git_refs._KINDS):
        names.setdefault(f"refs/iron-god/{kind}/<id>", f"LocalGitRefs {kind} anchor")
    return names


def directory_of(name: str) -> str:
    return name.rsplit("/", 1)[0] if name.endswith(">") else name


def test_the_scan_finds_the_names_it_is_meant_to_compare() -> None:
    assert lane_anchor_template() == "refs/iron-god/accepted"
    assert cumulative_baseline_ref() == "refs/iron-god/accepted"


@pytest.mark.xfail(strict=True, reason="#597: the lane's per-packet anchors and LocalGitRefs' cumulative baseline "
                   "collide (refs/iron-god/accepted vs refs/iron-god/accepted/<id>); fixed by renaming the baseline")
def test_no_ref_name_is_a_directory_of_another() -> None:
    names = all_ref_names()
    ref_files = [name for name in names if not name.endswith(">")]  # concrete single refs
    ref_dirs = {directory_of(name) for name in names if name.endswith(">")}
    collisions = sorted(f"{names[f]} ({f}) is also the directory of {d}/<id>" for f in ref_files for d in ref_dirs if f == d)

    assert collisions == []


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)


@pytest.mark.parametrize("first,second", [
    ("refs/iron-god/accepted/product-x-1", "refs/iron-god/accepted"),
    ("refs/iron-god/accepted", "refs/iron-god/accepted/product-x-1"),
])
def test_git_refuses_the_two_accepted_conventions_in_one_repository(tmp_path: Path, first: str, second: str) -> None:
    repo = tmp_path / "r"
    run_git(tmp_path, "init", "-q", "-b", "main", str(repo))
    commit = run_git(repo, "-c", "user.email=a@b", "-c", "user.name=x", "commit", "-q", "--allow-empty", "-m", "c")
    assert commit.returncode == 0
    sha = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    assert run_git(repo, "update-ref", first, sha).returncode == 0

    refused = run_git(repo, "update-ref", second, sha)

    assert refused.returncode != 0 and "exists; cannot create" in refused.stderr
