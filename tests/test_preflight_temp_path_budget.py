"""Regression protection for the Azathoth FULL-preflight Windows fixture path."""
from pathlib import Path
import tempfile

from engineering import preflight


def test_full_preflight_root_is_short_worktree_unique_and_exists():
    base = preflight.PREFLIGHT_BASETEMP
    assert base.is_dir()
    assert base.parent == Path(tempfile.gettempdir())
    assert base.name.startswith("ig-") and len(base.name) <= 12
    assert base.name[3:] == preflight._ROOT_KEY[:8]
    # The real pytest suite nests many fixture paths under this root.
    assert len(str(base / "main" / "test_a_desktop_control_the_res0" / "engine" / "_seed")) < 180
