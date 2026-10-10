from pathlib import Path
import pytest

from project_orchestrator.human_ownership import (
    AUTHORIZED,
    OWNER_CONTROL_ACTIVE,
    OWNER_CONTROL_UNVERIFIED,
    human_mode_active,
    human_mode_path,
    owner_control_status,
)


def test_absent_marker_allows_automation(tmp_path: Path):
    assert owner_control_status(tmp_path) == AUTHORIZED
    assert human_mode_active(tmp_path) is False


def test_explicit_owner_control_blocks(tmp_path: Path):
    human_mode_path(tmp_path).write_text('{"enabled": true}', encoding="utf-8")
    assert owner_control_status(tmp_path) == OWNER_CONTROL_ACTIVE
    assert human_mode_active(tmp_path) is True


def test_explicit_disabled_marker_authorizes_without_deletion(tmp_path: Path):
    marker = human_mode_path(tmp_path)
    original = '{"enabled": false, "source": "master-owner-control"}'
    marker.write_text(original, encoding="utf-8")
    assert owner_control_status(tmp_path) == AUTHORIZED
    assert human_mode_active(tmp_path) is False
    assert marker.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("payload", [
    "{partial",
    "{}",
    '{"enabled": "false"}',
    '{"enabled": 0}',
    '{"enabled": null}',
    "[]",
    "",
])
def test_unverified_marker_fails_closed(tmp_path: Path, payload: str):
    human_mode_path(tmp_path).write_text(payload, encoding="utf-8")
    assert owner_control_status(tmp_path) == OWNER_CONTROL_UNVERIFIED
    assert human_mode_active(tmp_path) is True


def test_bom_marker_is_supported(tmp_path: Path):
    human_mode_path(tmp_path).write_text(chr(0xFEFF) + '{"enabled": false}', encoding="utf-8")
    assert owner_control_status(tmp_path) == AUTHORIZED


def test_permission_failure_fails_closed(tmp_path: Path, monkeypatch):
    def deny(_self, *args, **kwargs):
        raise PermissionError("access denied")
    monkeypatch.setattr(Path, "read_text", deny)
    human_mode_path(tmp_path).touch()
    assert owner_control_status(tmp_path) == OWNER_CONTROL_UNVERIFIED
    assert human_mode_active(tmp_path) is True


def test_directory_as_marker_fails_closed(tmp_path: Path):
    human_mode_path(tmp_path).mkdir()
    assert owner_control_status(tmp_path) == OWNER_CONTROL_UNVERIFIED
