from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_candidate_refs import set_candidate
from project_orchestrator.iron_god_milestone_refs import create_milestone
from project_orchestrator.iron_god_dependency_resolution import (
    RESOLVED,
    UNRESOLVED,
    DependencyResolutionError,
    resolve_dependencies,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "a.txt").write_text("1\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "c1")
    return path


def test_dependency_accepted_in_git_resolves(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    create_milestone(repo, "M-dep", head)

    (result,) = resolve_dependencies(
        repo,
        "cap-a",
        ("cap-dep",),
        milestone_id_by_capability={"cap-dep": "M-dep"},
    )
    assert result.status == RESOLVED
    assert result.dependency_id == "cap-dep"
    assert result.capability_id == "cap-a"


def test_dependency_with_no_candidate_is_unresolved(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    (result,) = resolve_dependencies(
        repo,
        "cap-a",
        ("cap-dep",),
        milestone_id_by_capability={"cap-dep": "M-dep"},
    )
    assert result.status == UNRESOLVED


def test_dependency_candidate_unaccepted_is_unresolved(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "cap-dep", head, expected_current_sha=None)

    (result,) = resolve_dependencies(
        repo,
        "cap-a",
        ("cap-dep",),
        milestone_id_by_capability={"cap-dep": "M-dep"},
    )
    assert result.status == UNRESOLVED


def test_dependency_ambiguous_is_unresolved(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "c2")
    second = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "cap-dep", second, expected_current_sha=None)
    create_milestone(repo, "M-dep", first)

    (result,) = resolve_dependencies(
        repo,
        "cap-a",
        ("cap-dep",),
        milestone_id_by_capability={"cap-dep": "M-dep"},
    )
    assert result.status == UNRESOLVED


def test_unmapped_dependency_raises(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(DependencyResolutionError):
        resolve_dependencies(
            repo, "cap-a", ("cap-unmapped",), milestone_id_by_capability={}
        )


def test_empty_depends_on_yields_empty_result(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = resolve_dependencies(repo, "cap-a", (), milestone_id_by_capability={})
    assert result == ()


def test_multiple_dependencies_resolved_independently(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    create_milestone(repo, "M-ok", head)

    results = resolve_dependencies(
        repo,
        "cap-a",
        ("cap-ok", "cap-missing"),
        milestone_id_by_capability={"cap-ok": "M-ok", "cap-missing": "M-missing"},
    )
    by_id = {r.dependency_id: r for r in results}
    assert by_id["cap-ok"].status == RESOLVED
    assert by_id["cap-missing"].status == UNRESOLVED
