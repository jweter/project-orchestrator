"""Proves the real Knowledge Engine Core verifier substitute against a
disposable real Git repository, using this environment's own Python
(monkeypatched in place of the hardcoded KE-core venv path) so the
test runs real ruff/mypy/pytest without depending on that specific
venv existing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from project_orchestrator import (
    iron_god_verify_knowledge_engine_core as verifier_module,
)
from project_orchestrator.iron_god_verify_knowledge_engine_core import (
    KnowledgeEngineCoreVerificationError,
    verify_and_commit_knowledge_engine_core,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


@pytest.fixture(autouse=True)
def _use_local_python(monkeypatch):
    monkeypatch.setattr(
        verifier_module, "KNOWLEDGE_ENGINE_CORE_VENV_PYTHON", Path(sys.executable)
    )


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "ke-core-verifier-proof"
    (r / "knowledge_engine").mkdir(parents=True)
    (r / "tests").mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "proof@example.com")
    _git(r, "config", "user.name", "Proof")
    (r / "README.md").write_text("proof\n", encoding="utf-8")
    _git(r, "add", "README.md")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _task(repo: Path) -> dict:
    return {
        "id": "KE-CORE-VERIFIER-PROOF",
        "source": "knowledge_engine/hybrid_search.py",
        "tests": "tests/test_hybrid_search.py",
        "work": repo,
    }


_GOOD_SOURCE = (
    "from __future__ import annotations\n\n\n"
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n"
)
_GOOD_TEST = (
    "from knowledge_engine.hybrid_search import add\n\n\n"
    "def test_add() -> None:\n"
    "    assert add(2, 3) == 5\n"
)


def test_real_verification_succeeds_and_commits(repo):
    (repo / "knowledge_engine" / "hybrid_search.py").write_text(
        _GOOD_SOURCE, encoding="utf-8"
    )
    (repo / "tests" / "test_hybrid_search.py").write_text(_GOOD_TEST, encoding="utf-8")

    result = verify_and_commit_knowledge_engine_core(_task(repo))

    assert set(result.keys()) == {"head_sha", "receipt_sha256", "checks"}
    assert result["checks"] == 5
    assert len(result["receipt_sha256"]) == 64

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert result["head_sha"] == head

    staged_in_last_commit = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "HEAD",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert set(staged_in_last_commit) == {
        "knowledge_engine/hybrid_search.py",
        "tests/test_hybrid_search.py",
    }


def test_missing_source_file_raises_expected_code(repo):
    (repo / "tests" / "test_hybrid_search.py").write_text(_GOOD_TEST, encoding="utf-8")
    with pytest.raises(
        KnowledgeEngineCoreVerificationError, match="EXPECTED_SOURCE_OR_TEST_MISSING"
    ):
        verify_and_commit_knowledge_engine_core(_task(repo))


def test_failing_test_raises_check_failed(repo):
    (repo / "knowledge_engine" / "hybrid_search.py").write_text(
        _GOOD_SOURCE, encoding="utf-8"
    )
    (repo / "tests" / "test_hybrid_search.py").write_text(
        "from knowledge_engine.hybrid_search import add\n\n\n"
        "def test_add() -> None:\n"
        "    assert add(2, 3) == 999\n",
        encoding="utf-8",
    )
    with pytest.raises(
        KnowledgeEngineCoreVerificationError,
        match="KNOWLEDGE_ENGINE_CORE_CHECK_FAILED:pytest_focused",
    ):
        verify_and_commit_knowledge_engine_core(_task(repo))

    # A failed verification must never leave a real commit behind.
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "verified slice" not in log


def test_badly_formatted_source_raises_ruff_format_failure(repo):
    (repo / "knowledge_engine" / "hybrid_search.py").write_text(
        "def add(a,b):\n    return a+b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_hybrid_search.py").write_text(_GOOD_TEST, encoding="utf-8")
    with pytest.raises(
        KnowledgeEngineCoreVerificationError,
        match="KNOWLEDGE_ENGINE_CORE_CHECK_FAILED:ruff_format",
    ):
        verify_and_commit_knowledge_engine_core(_task(repo))
