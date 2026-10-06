from __future__ import annotations

import sys
from pathlib import Path

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
