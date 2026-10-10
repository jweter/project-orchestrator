import pytest
from pathlib import Path

from project_orchestrator.human_ownership import human_mode_active, human_mode_path


def test_missing_legacy_marker_allows_autonomous_ownership(tmp_path: Path):
    assert human_mode_active(tmp_path) is False


@pytest.mark.parametrize("content,blocked", [
    ('{"enabled": true}', True),
    ('{"enabled": false}', False),
    ('{"enabled": "false"}', True),
    ('{"enabled": 0}', True),
    ('{"enabled": null}', True),
    ('{}', True),
    ('{partial', True),
    ('', True),
])
def test_owner_marker_is_strict_and_fail_closed(tmp_path: Path, content: str, blocked: bool):
    human_mode_path(tmp_path).write_text(content, encoding="utf-8")
    assert human_mode_active(tmp_path) is blocked


def test_bom_prefixed_false_is_accepted(tmp_path: Path):
    human_mode_path(tmp_path).write_text('{"enabled": false}', encoding="utf-8-sig")
    assert human_mode_active(tmp_path) is False


def test_marker_read_failure_blocks_autonomy(tmp_path: Path):
    human_mode_path(tmp_path).mkdir()
    assert human_mode_active(tmp_path) is True
