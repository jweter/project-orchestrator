import os
from pathlib import Path

from project_orchestrator.human_ownership import (
    OWNER_CONTROL_UNVERIFIED,
    owner_control_status,
    owner_state_root,
)


def test_unmarked_state_never_resolves_live_owner_marker(tmp_path):
    """The conftest fixture keeps the live owner-control fallback out of tests."""
    root = owner_state_root(tmp_path / "builder.json")
    isolated = Path(os.environ["LOCALAPPDATA"]).resolve()
    assert isolated in root.resolve().parents
    assert isolated.parent == tmp_path.resolve().parent
    assert owner_control_status(root) == OWNER_CONTROL_UNVERIFIED
