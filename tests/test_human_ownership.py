from pathlib import Path

from project_orchestrator.human_ownership import human_mode_active, human_mode_path


def test_human_mode_absent_allows_autonomous_ownership(tmp_path: Path):
    assert human_mode_active(tmp_path) is False


def test_human_mode_marker_presence_blocks_autonomous_ownership(tmp_path: Path):
    path = human_mode_path(tmp_path)
    path.write_text('{"enabled": true}\n', encoding="utf-8")
    assert human_mode_active(tmp_path) is True


def test_malformed_marker_still_blocks_autonomous_ownership(tmp_path: Path):
    human_mode_path(tmp_path).write_text("{partial", encoding="utf-8")
    assert human_mode_active(tmp_path) is True


def test_even_disabled_payload_requires_explicit_marker_removal(tmp_path: Path):
    human_mode_path(tmp_path).write_text('{"enabled": false}\n', encoding="utf-8")
    assert human_mode_active(tmp_path) is True
