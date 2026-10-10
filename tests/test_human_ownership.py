from pathlib import Path

from project_orchestrator.human_ownership import (
    AUTHORIZED,
    OWNER_CONTROL_ACTIVE,
    OWNER_CONTROL_UNVERIFIED,
    human_mode_active,
    human_mode_path,
    owner_control_status,
)


def test_human_mode_absent_allows_autonomous_ownership(tmp_path: Path):
    assert owner_control_status(tmp_path) == AUTHORIZED
    assert human_mode_active(tmp_path) is False


def test_human_mode_marker_enabled_true_blocks_autonomous_ownership(tmp_path: Path):
    path = human_mode_path(tmp_path)
    path.write_text('{"enabled": true}\n', encoding="utf-8")
    assert owner_control_status(tmp_path) == OWNER_CONTROL_ACTIVE
    assert human_mode_active(tmp_path) is True


def test_malformed_marker_fails_closed(tmp_path: Path):
    human_mode_path(tmp_path).write_text("{partial", encoding="utf-8")
    assert owner_control_status(tmp_path) == OWNER_CONTROL_UNVERIFIED
    assert human_mode_active(tmp_path) is True


def test_disabled_payload_releases_autonomous_ownership(tmp_path: Path):
    human_mode_path(tmp_path).write_text('{"enabled": false}\n', encoding="utf-8")
    assert owner_control_status(tmp_path) == AUTHORIZED
    assert human_mode_active(tmp_path) is False


def test_string_false_is_unverified_and_blocks(tmp_path: Path):
    human_mode_path(tmp_path).write_text('{"enabled": "false"}\n', encoding="utf-8")
    assert owner_control_status(tmp_path) == OWNER_CONTROL_UNVERIFIED
    assert human_mode_active(tmp_path) is True
