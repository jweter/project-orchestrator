"""User-scoped Windows DPAPI storage for a manually approved Notion token.

Never write credentials into a repository, logs, SQLite, or a task message.
This module is inert on import and will refuse non-Windows machines by default.
"""
from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from .notion_n02_transport import validate_token

_APP_DIR = ("ExponentialEngine", "IronGodLocal", "Integrations")
_FILENAME = "notion-knowledge-bridge.dpapi"


class CredentialStoreError(RuntimeError):
    """Safe, credential-free storage error."""


class _Blob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def default_token_path() -> Path:
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        raise CredentialStoreError("Windows user profile is required")
    return Path(os.environ["LOCALAPPDATA"]).joinpath(*_APP_DIR, _FILENAME)


def _bytes_blob(payload: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(payload)
    return _Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_bytes(payload: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("DPAPI is only available on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source, keepalive = _bytes_blob(payload)
    output = _Blob()
    proto = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    if decrypt:
        crypt = crypt32.CryptUnprotectData
    else:
        crypt = crypt32.CryptProtectData
    crypt.argtypes = proto
    crypt.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # No user prompts or GUI dialogs from DPAPI. Scope is current Windows user;
    # no CRYPTPROTECT_LOCAL_MACHINE flag is used.
    ok = crypt(
        ctypes.byref(source), None, None, None, None,
        0x1, ctypes.byref(output),
    )
    _ = keepalive
    if not ok:
        raise CredentialStoreError("Windows credential protection failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def protect_token(plaintext: bytes) -> bytes:
    return _dpapi_bytes(plaintext, decrypt=False)


def unprotect_token(encrypted: bytes) -> bytes:
    return _dpapi_bytes(encrypted, decrypt=True)


def save_token(
    token: str,
    *,
    path: Path | None = None,
    protect: Callable[[bytes], bytes] = protect_token,
) -> Path:
    """Explicit user-invoked save; atomic replacement of ciphertext only."""
    validated = validate_token(token)
    destination = path if path is not None else default_token_path()
    if destination.is_symlink() or destination.parent.is_symlink():
        raise CredentialStoreError("Refusing symlink credential destination")
    ciphertext = protect(validated.encode("ascii"))
    if not ciphertext or ciphertext == validated.encode("ascii"):
        raise CredentialStoreError("Protection returned invalid ciphertext")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".notion-", suffix=".tmp", dir=destination.parent)
        temp_path = Path(name)
        with os.fdopen(fd, "wb") as output:
            output.write(ciphertext)
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return destination


def load_token(
    *, path: Path | None = None,
    unprotect: Callable[[bytes], bytes] = unprotect_token,
) -> str:
    """Decryption occurs only for an explicit user-invoked test."""
    destination = path if path is not None else default_token_path()
    if destination.is_symlink() or not destination.is_file():
        raise CredentialStoreError("No saved credential was found")
    if destination.stat().st_size > 8192:
        raise CredentialStoreError("Credential file exceeds safety limit")
    encrypted = destination.read_bytes()
    try:
        return validate_token(unprotect(encrypted).decode("ascii"))
    except (UnicodeError, ValueError) as exc:
        raise CredentialStoreError("Stored credential is not valid") from exc
