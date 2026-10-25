from __future__ import annotations

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
