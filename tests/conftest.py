from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PACKAGE_ROOT = (REPO_ROOT / "src" / "project_orchestrator").resolve()
SRC_ROOT = str((REPO_ROOT / "src").resolve())
if SRC_ROOT in sys.path:
    sys.path.remove(SRC_ROOT)
sys.path.insert(0, SRC_ROOT)

import project_orchestrator  # noqa: E402
from project_orchestrator import iron_god_dev_mode  # noqa: E402


def _package_root() -> Path:
    package_file = getattr(project_orchestrator, "__file__", None)
    if not package_file:
        raise RuntimeError("project_orchestrator has no import provenance")
    return Path(package_file).resolve().parent


def pytest_sessionstart(session) -> None:
    actual = _package_root()
    if actual != EXPECTED_PACKAGE_ROOT:
        raise RuntimeError(
            "IMPORT_PROVENANCE_MISMATCH: pytest imported project_orchestrator "
            f"from {actual}, expected {EXPECTED_PACKAGE_ROOT}. "
            "Refusing to test a different checkout."
        )


ROUTINE_TOKEN_PREFIX = "CLAUDE_ROUTINE_"


@pytest.fixture(autouse=True)
def _isolate_live_owner_state(tmp_path_factory, monkeypatch):
    """Never let a test resolve the machine's live owner-control state.

    owner_state_root falls back to %LOCALAPPDATA%\\ExponentialEngine\\flow-state,
    so an unisolated test reads the real owner marker and its result depends
    on whether the owner currently holds the laptop. An empty per-test root
    yields OWNER_CONTROL_UNVERIFIED (fail closed); tests that exercise
    downstream behavior must inject AUTHORIZED explicitly.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path_factory.mktemp("localappdata")))
    # The single model slot and the memory-pressure stamp beside it default to the home folder: never the live ones.
    monkeypatch.setenv("EXPONENTIAL_OLLAMA_LOCK_DIR", str(tmp_path_factory.mktemp("ollama-locks") / "ollama-aider"))
    # A developer machine holds the real routine tokens. A test that reaches the capable-builder dispatch with them set
    # fires a real, paid cloud run carrying fixture data (measured 2026-11-12: a release preflight opened
    # rocksmith-cdlc-generator#764 from the 'cap-extra/extra-helper' fixture). No test inherits them; a test that needs
    # a token sets its own fake one.
    for name in [key for key in os.environ if key.startswith(ROUTINE_TOKEN_PREFIX)]:
        monkeypatch.delenv(name)
    # Dev Mode closes and relaunches the owner's real programs. A lane test that reaches a memory wait with the shipped
    # policy ran the real thing (measured 2026-11-13: a release preflight closed the owner's Chrome four times, plus
    # ChatGPT, Edge and OneDrive, while they were using the laptop). No test sees the machine's processes, and no test
    # can close or launch one; a test that exercises Dev Mode passes its own fakes.
    monkeypatch.setattr(iron_god_dev_mode, "snapshot_processes", lambda: [])
    monkeypatch.setattr(iron_god_dev_mode, "system_closer", _forbidden_program_control)
    monkeypatch.setattr(iron_god_dev_mode, "launcher", _forbidden_program_control)
    monkeypatch.setattr(iron_god_dev_mode, "alive_pids", lambda pids: set())


def _forbidden_program_control(*_args, **_kwargs):
    raise AssertionError("a test tried to close or launch a real program on this machine")
