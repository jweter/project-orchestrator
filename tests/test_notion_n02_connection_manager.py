"""N02 GUI regression tests: saving does not require page permission or HTTP."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from unittest.mock import Mock

import pytest

import project_orchestrator.notion_n02_connection_manager as gui
from project_orchestrator.notion_n02_transport import NotionConnectionError

TOKEN = "ntn_" + "X" * 40


@pytest.fixture
def application() -> gui.ConnectionManager:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display not available")
    root.withdraw()
    app = gui.ConnectionManager(root)
    yield app
    root.destroy()


def test_save_enabled_with_well_formed_token_without_network(
    application: gui.ConnectionManager, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = application
    assert str(app.save_button.cget("state")) == "disabled"
    app.token_var.set("invalid")
    assert str(app.save_button.cget("state")) == "disabled"
    app.token_var.set(TOKEN)
    assert str(app.save_button.cget("state")) == "normal"

    destination = tmp_path / "example.dpapi"
    stub = Mock(return_value=destination)
    monkeypatch.setattr(gui, "save_token", stub)
    app.save_current_token()
    stub.assert_called_once_with(TOKEN)
    assert app.status_var.get().startswith("SAVED: Encrypted credential stored")
    assert app.token_var.get() == ""
    assert str(app.save_button.cget("state")) == "disabled"


def test_page_404_is_distinguished_from_successful_authentication(
    application: gui.ConnectionManager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, _: str) -> None:
            pass

        def check_authentication(self) -> None:
            return

        def read_approved_page(self, *args: object, **kwargs: object) -> None:
            raise NotionConnectionError("HTTP_404_PAGE_ACCESS")

    monkeypatch.setattr(gui, "NotionReadOnlyClient", FakeClient)
    application._worker(TOKEN, saved=False)
    state, message, fingerprint, saved = application._messages.get_nowait()
    assert state == "page_error"
    assert "Authentication PASS" in message
    assert "404" in message
    assert TOKEN not in message
    assert fingerprint is None
    assert saved is False


def test_authentication_401_is_distinguished_from_page_access(
    application: gui.ConnectionManager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, _: str) -> None:
            pass

        def check_authentication(self) -> None:
            raise NotionConnectionError("HTTP_401_AUTHENTICATION")

    monkeypatch.setattr(gui, "NotionReadOnlyClient", FakeClient)
    application._worker(TOKEN, saved=False)
    state, message, fingerprint, saved = application._messages.get_nowait()
    assert state == "error"
    assert "Authentication failed" in message
    assert "401" in message
    assert TOKEN not in message


def test_no_saved_credential_message_is_actionable(
    application: gui.ConnectionManager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from project_orchestrator.notion_n02_secret_store import CredentialStoreError

    def missing() -> str:
        raise CredentialStoreError("No saved credential was found")

    monkeypatch.setattr(gui, "load_token", missing)
    application.test_saved_token()
    assert "NO SAVED CREDENTIAL" in application.status_var.get()
    assert "Save securely" in application.status_var.get()
