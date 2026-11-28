"""Credential handling tests; never use a real Notion token."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from project_orchestrator.notion_n02_secret_store import (
    CredentialStoreError,
    default_token_path,
    load_token,
    save_token,
    protect_token,
)

TOKEN = "ntn_" + "Z" * 42


def test_explicit_save_and_load_use_ciphertext_only(tmp_path: Path) -> None:
    target = tmp_path / "token.dpapi"
    seen = []
    def encrypt(value: bytes) -> bytes:
        seen.append(value)
        return b"ENCRYPTED" + value[::-1]
    def decrypt(value: bytes) -> bytes:
        assert value.startswith(b"ENCRYPTED")
        return value[len(b"ENCRYPTED"):][::-1]

    result = save_token(TOKEN, path=target, protect=encrypt)
    assert result == target
    assert target.read_bytes().startswith(b"ENCRYPTED")
    assert TOKEN.encode() not in target.read_bytes()
    assert len(seen) == 1
    assert load_token(path=target, unprotect=decrypt) == TOKEN
    assert sorted(p.name for p in tmp_path.iterdir()) == ["token.dpapi"]


def test_store_rejects_unprotected_plaintext(tmp_path: Path) -> None:
    with pytest.raises(CredentialStoreError, match="Protection"):
        save_token(TOKEN, path=tmp_path / "token.dpapi", protect=lambda b: b)


def test_store_rejects_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text("untouched")
    link = tmp_path / "token.dpapi"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not available")
    with pytest.raises(CredentialStoreError, match="symlink"):
        save_token(TOKEN, path=link, protect=lambda b: b"ENCRYPTED")


def test_missing_and_oversized_credentials_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "token.dpapi"
    with pytest.raises(CredentialStoreError, match="No saved"):
        load_token(path=path, unprotect=lambda b: b)
    path.write_bytes(b"x" * 8193)
    with pytest.raises(CredentialStoreError, match="safety limit"):
        load_token(path=path, unprotect=lambda b: b)


def test_rejected_input_does_not_create_file(tmp_path: Path) -> None:
    path = tmp_path / "token.dpapi"
    from project_orchestrator.notion_n02_transport import NotionConnectionError
    with pytest.raises(NotionConnectionError):
        save_token("ntn_invalid\n", path=path, protect=lambda b: b"ENCRYPTED")
    assert not path.exists()


def test_default_path_requires_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        pytest.skip("Default Windows LOCALAPPDATA is environment dependent")
    with pytest.raises(CredentialStoreError, match="Windows user profile"):
        default_token_path()


def test_dpapi_refuses_non_windows() -> None:
    if os.name == "nt":
        pytest.skip("DPAPI roundtrip covered by Windows native smoke")
    with pytest.raises(CredentialStoreError, match="only available on Windows"):
        protect_token(TOKEN.encode())
