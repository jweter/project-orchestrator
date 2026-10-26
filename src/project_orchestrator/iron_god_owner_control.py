"""Authoritative Iron God owner execution controller.

Owner decision (2026-10-25): this controller is the only authority that can
permit autonomous execution. The retired owner marker and exclusivity files
grant nothing. Two owner commands exist, issued by the desktop icons:

* ``TAKE_THE_WHEEL`` grants execution. It must originate from the active
  console session through the desktop-control launch chain
  (explorer -> wscript -> powershell -> python), is bound to the accepted
  release SHA, and only becomes effective once the running supervisor
  acknowledges it within ``ACK_WINDOW``. The grant is bound to that exact
  supervisor process (pid + OS start time): if the supervisor exits, the grant
  is dead and no restart, watchdog or scheduled task can revive it.
* ``GIVE_ME_CONTROL`` revokes immediately, from any process of the owner's
  account (revocation is the fail-safe direction). The supervisor records a
  revocation acknowledgment after it has stopped dispatch.

Authentication: every command and acknowledgment carries an HMAC-SHA256 over
its canonical payload. The key is 32 random bytes protected with Windows DPAPI
for the owner's account and stored with the owner's SID in a control
directory whose ACL admits only that account and SYSTEM. A JSON file or
database row written without the key is never authority. Replay protection:
unique command IDs and nonces, a strictly increasing generation enforced in
one transaction, and a freshness window on issuance.

Threat model limit: any code already running as the owner's Windows account
in the owner's session can use DPAPI as the owner, exactly as it could click
the desktop icon. The controller defends against forged, stale, replayed,
mis-bound and unauthenticated authority, and against restarts reviving it;
it does not defend against code with the owner's own credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .recovery_manager import process_start_fingerprint

TAKE_THE_WHEEL = "TAKE_THE_WHEEL"
GIVE_ME_CONTROL = "GIVE_ME_CONTROL"
ACTIONS = frozenset({TAKE_THE_WHEEL, GIVE_ME_CONTROL})

COMMAND_FRESHNESS = timedelta(seconds=120)
CLOCK_SKEW = timedelta(seconds=5)
ACK_WINDOW = timedelta(seconds=180)
GRANT_MAX_AGE = timedelta(hours=24)

CONTROL_DIRNAME = "IronGodControl"
KEY_FILE = "owner-key.dpapi"
ENROLLMENT_FILE = "owner-enrollment.json"
DB_FILE = "owner-control.sqlite"
SCHEMA_VERSION = 1
_DPAPI_ENTROPY = b"iron-god-owner-control/v1"
_SYSTEM_SID = "S-1-5-18"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEX = re.compile(r"[0-9a-f]{32}\Z")

# Owner statuses consumed by human_ownership and the execution decision.
AUTHORIZED = "AUTHORIZED"
AUTHORIZED_PENDING_ACK = "AUTHORIZED_PENDING_ACK"
OWNER_CONTROL_ACTIVE = "OWNER_CONTROL_ACTIVE"
UNVERIFIED = "UNVERIFIED"


class OwnerControlError(RuntimeError):
    """A command or acknowledgment was refused; the code says why."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SupervisorIdentity:
    instance_id: str
    pid: int
    process_start_fingerprint: str
    host: str = field(default_factory=socket.gethostname)

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "pid": self.pid,
            "process_start_fingerprint": self.process_start_fingerprint,
            "host": self.host,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SupervisorIdentity":
        try:
            identity = cls(
                instance_id=str(value["instance_id"]),
                pid=int(value["pid"]),
                process_start_fingerprint=str(value["process_start_fingerprint"]),
                host=str(value["host"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OwnerControlError("SUPERVISOR_IDENTITY_INVALID") from exc
        if not identity.instance_id or identity.pid <= 0 or not identity.process_start_fingerprint:
            raise OwnerControlError("SUPERVISOR_IDENTITY_INVALID")
        return identity


@dataclass(frozen=True)
class OwnerDecision:
    status: str
    permitted: bool
    reasons: tuple[str, ...]
    generation: int | None = None
    command_id: str | None = None
    action: str | None = None
    release_sha: str | None = None
    supervisor_instance_id: str | None = None
    revocation_acknowledged: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "permitted": self.permitted,
            "reasons": list(self.reasons),
            "generation": self.generation,
            "command_id": self.command_id,
            "action": self.action,
            "release_sha": self.release_sha,
            "supervisor_instance_id": self.supervisor_instance_id,
            "revocation_acknowledged": self.revocation_acknowledged,
        }


# --- paths -----------------------------------------------------------------

def control_root(state_root: Path | None = None) -> Path:
    """Controller directory inside one ``flow-state`` root (one authority per root)."""
    if state_root is not None:
        return Path(state_root) / CONTROL_DIRNAME
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise OwnerControlError("LOCALAPPDATA_UNAVAILABLE")
    return Path(local) / "ExponentialEngine" / "flow-state" / CONTROL_DIRNAME


def _clock(now: datetime | None) -> datetime:
    current = now if now is not None else datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    return current.astimezone(UTC)


def _parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise OwnerControlError("TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise OwnerControlError("TIMESTAMP_NOT_AWARE")
    return parsed.astimezone(UTC)


# --- Windows identity, DPAPI, session and ancestry ---------------------------

def _require_windows() -> None:
    if os.name != "nt":
        raise OwnerControlError("WINDOWS_OWNER_CONTROL_UNAVAILABLE")


def current_user_sid() -> str:
    _require_windows()
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    ]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OwnerControlError("OWNER_TOKEN_UNAVAILABLE")
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise OwnerControlError("OWNER_TOKEN_UNAVAILABLE")
        psid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(text)):
            raise OwnerControlError("OWNER_SID_UNAVAILABLE")
        try:
            return str(text.value)
        finally:
            kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel32.CloseHandle(token)


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    _require_windows()
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> tuple[Blob, Any]:
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    signature = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
    ]
    crypt32.CryptProtectData.argtypes = signature
    crypt32.CryptUnprotectData.argtypes = signature
    source, _source_buffer = blob(data)
    entropy, _entropy_buffer = blob(_DPAPI_ENTROPY)
    out = Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN: never prompt; fail closed instead.
    ok = function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OwnerControlError("OWNER_KEY_PROTECTION_FAILED" if protect else "OWNER_KEY_UNREADABLE")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _process_table() -> dict[int, tuple[int, str]]:
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise OwnerControlError("PROCESS_SNAPSHOT_UNAVAILABLE")
    table: dict[int, tuple[int, str]] = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            table[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile.lower())
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def process_ancestry(pid: int | None = None) -> list[str]:
    """Executable names from this process upward (bounded, cycle-safe)."""
    _require_windows()
    table = _process_table()
    names: list[str] = []
    current = pid or os.getpid()
    seen: set[int] = set()
    while current in table and current not in seen and len(names) < 24:
        seen.add(current)
        parent, name = table[current]
        names.append(name)
        current = parent
    return names


def verify_active_console_session() -> int:
    """Refuse services, session 0 and disconnected/remote sessions."""
    _require_windows()
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    session = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise OwnerControlError("SESSION_UNAVAILABLE")
    console = kernel32.WTSGetActiveConsoleSessionId()
    if session.value == 0 or session.value != console:
        raise OwnerControlError("NOT_ACTIVE_CONSOLE_SESSION")
    return int(session.value)


def verify_interactive_desktop_origin() -> dict[str, Any]:
    """Refuse unless this process is the owner's desktop-icon launch.

    Requires the active console session (not a service or remote session)
    and a desktop-control ancestry: wscript.exe launched by explorer.exe.
    """
    session_id = verify_active_console_session()
    ancestry = process_ancestry()
    try:
        launcher = ancestry.index("wscript.exe")
    except ValueError as exc:
        raise OwnerControlError("NOT_DESKTOP_CONTROL_ORIGIN") from exc
    if "explorer.exe" not in ancestry[launcher + 1:launcher + 2]:
        raise OwnerControlError("NOT_DESKTOP_CONTROL_ORIGIN")
    return {"session_id": session_id, "origin": "desktop-control", "ancestry": ancestry[: launcher + 2]}


def remote_or_local_origin() -> dict[str, Any]:
    """Origin record for revocation, which any owner process may request."""
    session: int | None = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        value = wintypes.DWORD()
        if ctypes.WinDLL("kernel32").ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
            session = int(value.value)
    return {"session_id": session, "origin": "owner-process"}


def supervisor_alive(identity: SupervisorIdentity) -> bool:
    """Same host, same pid, same OS start time, and not yet exited."""
    if identity.host != socket.gethostname():
        return False
    if os.name != "nt":
        return process_start_fingerprint(identity.pid) == identity.process_start_fingerprint
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, identity.pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return False
    finally:
        kernel32.CloseHandle(handle)
    return process_start_fingerprint(identity.pid) == identity.process_start_fingerprint


def current_supervisor_identity(instance_id: str) -> SupervisorIdentity:
    fingerprint = process_start_fingerprint(None)
    if not fingerprint:
        raise OwnerControlError("SUPERVISOR_IDENTITY_UNAVAILABLE")
    return SupervisorIdentity(instance_id=instance_id, pid=os.getpid(), process_start_fingerprint=fingerprint)


# --- control directory, key and store ----------------------------------------

def _restrict_acl(root: Path, owner_sid: str) -> None:
    subprocess.run(
        ["icacls", str(root), "/inheritance:r", "/grant:r",
         f"*{owner_sid}:(OI)(CI)F", f"*{_SYSTEM_SID}:(OI)(CI)F", "/Q"],
        capture_output=True, text=True, check=True, timeout=30,
    )


def directory_acl(root: Path) -> list[tuple[int, int, str]]:
    """(ace_type, ace_flags, sid) for every ACE in the directory's DACL."""
    _require_windows()
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    # SE_FILE_OBJECT=1, DACL_SECURITY_INFORMATION=4
    status = advapi32.GetNamedSecurityInfoW(
        str(root), 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)
    )
    if status != 0 or not dacl.value:
        raise OwnerControlError("CONTROL_DIRECTORY_ACL_UNREADABLE")
    try:
        # ACL header: AclRevision(1) Sbz1(1) AclSize(2) AceCount(2) Sbz2(2)
        count = ctypes.cast(dacl, ctypes.POINTER(ctypes.c_uint16))[2]
        entries: list[tuple[int, int, str]] = []
        for index in range(count):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise OwnerControlError("CONTROL_DIRECTORY_ACL_UNREADABLE")
            header = ctypes.cast(ace, ctypes.POINTER(ctypes.c_uint8))
            ace_type, ace_flags = int(header[0]), int(header[1])
            sid_text = ""
            if ace_type in (0, 1):  # ACCESS_ALLOWED / ACCESS_DENIED: Mask then SID at +8
                text = wintypes.LPWSTR()
                if advapi32.ConvertSidToStringSidW(ace.value + 8, ctypes.byref(text)):
                    try:
                        sid_text = str(text.value)
                    finally:
                        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
            entries.append((ace_type, ace_flags, sid_text))
        return entries
    finally:
        kernel32.LocalFree(descriptor)


def _acl_is_restricted(root: Path, owner_sid: str) -> bool:
    """Only explicit allow ACEs for the owner and SYSTEM; nothing inherited."""
    try:
        entries = directory_acl(root)
    except OwnerControlError:
        return False
    allowed = {owner_sid, _SYSTEM_SID}
    return bool(entries) and all(
        ace_type == 0 and not ace_flags & 0x10 and sid in allowed
        for ace_type, ace_flags, sid in entries
    )


def enroll_owner(root: Path) -> dict[str, Any]:
    """Create the control directory, ACL and DPAPI key once, as the owner."""
    owner_sid = current_user_sid()
    root.mkdir(parents=True, exist_ok=True)
    enrollment_path = root / ENROLLMENT_FILE
    key_path = root / KEY_FILE
    if enrollment_path.exists() or key_path.exists():
        return load_enrollment(root)
    _restrict_acl(root, owner_sid)
    key = secrets.token_bytes(32)
    key_path.write_bytes(_dpapi(key, protect=True))
    enrollment = {
        "schema_version": SCHEMA_VERSION,
        "owner_sid": owner_sid,
        "key_id": hashlib.sha256(key).hexdigest()[:16],
        "enrolled_at_utc": datetime.now(UTC).isoformat(),
    }
    enrollment_path.write_text(json.dumps(enrollment, sort_keys=True), encoding="utf-8")
    return enrollment


def load_enrollment(root: Path) -> dict[str, Any]:
    try:
        enrollment = json.loads((root / ENROLLMENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OwnerControlError("OWNER_NOT_ENROLLED") from exc
    if not isinstance(enrollment, dict) or not isinstance(enrollment.get("owner_sid"), str):
        raise OwnerControlError("OWNER_ENROLLMENT_INVALID")
    return enrollment


def _owner_key(root: Path) -> bytes:
    enrollment = load_enrollment(root)
    if enrollment["owner_sid"] != current_user_sid():
        raise OwnerControlError("NOT_ENROLLED_OWNER")
    if not _acl_is_restricted(root, enrollment["owner_sid"]):
        raise OwnerControlError("CONTROL_DIRECTORY_ACL_UNSAFE")
    try:
        key = _dpapi((root / KEY_FILE).read_bytes(), protect=False)
    except OSError as exc:
        raise OwnerControlError("OWNER_KEY_UNREADABLE") from exc
    if len(key) != 32 or hashlib.sha256(key).hexdigest()[:16] != enrollment.get("key_id"):
        raise OwnerControlError("OWNER_KEY_MISMATCH")
    return key


def _connect(root: Path) -> sqlite3.Connection:
    db = sqlite3.connect(root / DB_FILE, timeout=10, isolation_level=None)
    db.execute("PRAGMA synchronous=FULL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS owner_commands ("
        " generation INTEGER PRIMARY KEY,"
        " command_id TEXT NOT NULL UNIQUE,"
        " action TEXT NOT NULL CHECK (action IN ('TAKE_THE_WHEEL','GIVE_ME_CONTROL')),"
        " payload TEXT NOT NULL, mac TEXT NOT NULL, recorded_at_utc TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS supervisor_acks ("
        " generation INTEGER PRIMARY KEY REFERENCES owner_commands(generation),"
        " payload TEXT NOT NULL, mac TEXT NOT NULL, recorded_at_utc TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS control_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL,"
        " kind TEXT NOT NULL, generation INTEGER, detail TEXT NOT NULL)"
    )
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return db


def _connect_read_only(root: Path) -> sqlite3.Connection:
    """Decisions and observations never create, migrate or modify the store."""
    return sqlite3.connect((root / DB_FILE).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _mac(key: bytes, canonical: str) -> str:
    return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _event(db: sqlite3.Connection, kind: str, generation: int | None, detail: Mapping[str, Any], now: datetime) -> None:
    db.execute(
        "INSERT INTO control_events (at_utc, kind, generation, detail) VALUES (?,?,?,?)",
        (now.isoformat(), kind, generation, _canonical(detail)),
    )


def _verified(key: bytes, payload_text: str, mac: str) -> dict[str, Any]:
    if not hmac.compare_digest(_mac(key, payload_text), mac):
        raise OwnerControlError("AUTHENTICATION_FAILED")
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise OwnerControlError("PAYLOAD_INVALID")
    return payload


# --- owner commands ----------------------------------------------------------

def issue_command(
    root: Path,
    action: str,
    *,
    release_sha: str | None,
    origin_check: Callable[[], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record one authenticated owner command; returns its public summary."""
    if action not in ACTIONS:
        raise OwnerControlError("ACTION_UNSUPPORTED")
    current = _clock(now)
    if action == TAKE_THE_WHEEL:
        if release_sha is None or _SHA.fullmatch(release_sha) is None:
            raise OwnerControlError("RELEASE_SHA_INVALID")
        origin = (origin_check or verify_interactive_desktop_origin)()
    else:
        if release_sha is not None and _SHA.fullmatch(release_sha) is None:
            raise OwnerControlError("RELEASE_SHA_INVALID")
        origin = (origin_check or remote_or_local_origin)()
    enrollment = enroll_owner(root)
    key = _owner_key(root)
    with closing(_connect(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT MAX(generation) FROM owner_commands").fetchone()
            generation = int(row[0] or 0) + 1
            payload = {
                "schema_version": SCHEMA_VERSION,
                "command_id": uuid4().hex,
                "nonce": secrets.token_hex(16),
                "action": action,
                "generation": generation,
                "release_sha": release_sha,
                "issued_at_utc": current.isoformat(),
                "owner_sid": enrollment["owner_sid"],
                "session_id": origin.get("session_id"),
                "origin": origin.get("origin"),
            }
            text = _canonical(payload)
            db.execute(
                "INSERT INTO owner_commands (generation, command_id, action, payload, mac, recorded_at_utc)"
                " VALUES (?,?,?,?,?,?)",
                (generation, payload["command_id"], action, text, _mac(key, text), current.isoformat()),
            )
            _event(db, "OWNER_COMMAND_RECORDED", generation,
                   {"action": action, "command_id": payload["command_id"], "origin": payload["origin"]}, current)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return {"command_id": payload["command_id"], "generation": generation, "action": action,
            "release_sha": release_sha, "issued_at_utc": payload["issued_at_utc"]}


def submit_signed_command(root: Path, payload_text: str, mac: str, *, now: datetime | None = None) -> int:
    """Accept an externally produced command only if authentic, fresh and next.

    Used to prove that replayed, reordered, forged or stale commands fail.
    """
    current = _clock(now)
    key = _owner_key(root)
    payload = _verified(key, payload_text, mac)
    issued = _parse_time(payload.get("issued_at_utc"))
    if not (current - COMMAND_FRESHNESS <= issued <= current + CLOCK_SKEW):
        raise OwnerControlError("COMMAND_NOT_FRESH")
    if payload.get("action") not in ACTIONS or payload.get("owner_sid") != load_enrollment(root)["owner_sid"]:
        raise OwnerControlError("COMMAND_IDENTITY_INVALID")
    with closing(_connect(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            expected = int(db.execute("SELECT MAX(generation) FROM owner_commands").fetchone()[0] or 0) + 1
            if payload.get("generation") != expected:
                raise OwnerControlError("COMMAND_GENERATION_STALE")
            try:
                db.execute(
                    "INSERT INTO owner_commands (generation, command_id, action, payload, mac, recorded_at_utc)"
                    " VALUES (?,?,?,?,?,?)",
                    (expected, payload["command_id"], payload["action"], payload_text, mac, current.isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise OwnerControlError("COMMAND_REPLAYED") from exc
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return expected


def _latest(db: sqlite3.Connection) -> tuple[int, str, str] | None:
    return db.execute(
        "SELECT generation, payload, mac FROM owner_commands ORDER BY generation DESC LIMIT 1"
    ).fetchone()


def _ack(db: sqlite3.Connection, generation: int) -> tuple[str, str] | None:
    return db.execute(
        "SELECT payload, mac FROM supervisor_acks WHERE generation=?", (generation,)
    ).fetchone()


def _denied(status: str, reason: str, **fields: Any) -> OwnerDecision:
    return OwnerDecision(status=status, permitted=False, reasons=(reason,), **fields)


def evaluate(
    root: Path,
    *,
    release_sha: str | None = None,
    supervisor: SupervisorIdentity | None = None,
    now: datetime | None = None,
    liveness: Callable[[SupervisorIdentity], bool] = supervisor_alive,
) -> OwnerDecision:
    """Fresh, read-only decision. ``permitted`` only for a live acked grant."""
    current = _clock(now)
    try:
        key = _owner_key(root)
        if not (root / DB_FILE).is_file():
            return _denied(UNVERIFIED, "NO_OWNER_COMMAND")
        with closing(_connect_read_only(root)) as db:
            latest = _latest(db)
            if latest is None:
                return _denied(UNVERIFIED, "NO_OWNER_COMMAND")
            generation, payload_text, mac = latest
            command = _verified(key, payload_text, mac)
            if command.get("generation") != generation:
                raise OwnerControlError("COMMAND_GENERATION_MISMATCH")
            ack_row = _ack(db, generation)
    except OwnerControlError as exc:
        return _denied(UNVERIFIED, exc.code)
    except (OSError, sqlite3.Error, ValueError):
        return _denied(UNVERIFIED, "CONTROLLER_STATE_UNREADABLE")
    fields = {
        "generation": generation,
        "command_id": command.get("command_id"),
        "action": command.get("action"),
        "release_sha": command.get("release_sha"),
    }
    try:
        ack = _verified(key, *ack_row) if ack_row is not None else None
    except OwnerControlError as exc:
        return _denied(UNVERIFIED, "ACK_" + exc.code, **fields)
    if ack is not None and (ack.get("generation") != generation or ack.get("command_id") != command.get("command_id")):
        return _denied(UNVERIFIED, "ACK_BINDING_INVALID", **fields)
    if command.get("action") == GIVE_ME_CONTROL:
        return OwnerDecision(
            status=OWNER_CONTROL_ACTIVE, permitted=False, reasons=("OWNER_CONTROL_ACTIVE",),
            revocation_acknowledged=bool(ack and ack.get("kind") == "REVOKE_ACK"),
            supervisor_instance_id=(ack or {}).get("supervisor", {}).get("instance_id"), **fields,
        )
    issued = _parse_time(command.get("issued_at_utc"))
    age = current - issued
    if age > GRANT_MAX_AGE:
        return _denied(UNVERIFIED, "GRANT_EXPIRED", **fields)
    if release_sha is not None and command.get("release_sha") != release_sha:
        return _denied(UNVERIFIED, "GRANT_RELEASE_MISMATCH", **fields)
    if ack is None:
        if age <= ACK_WINDOW:
            return _denied(AUTHORIZED_PENDING_ACK, "SUPERVISOR_ACK_PENDING", **fields)
        return _denied(UNVERIFIED, "SUPERVISOR_ACK_MISSING", **fields)
    if ack.get("kind") != "GRANT_ACK" or ack.get("release_sha") != command.get("release_sha"):
        return _denied(UNVERIFIED, "ACK_BINDING_INVALID", **fields)
    acked_by = SupervisorIdentity.from_mapping(ack.get("supervisor") or {})
    fields["supervisor_instance_id"] = acked_by.instance_id
    if supervisor is not None and supervisor != acked_by:
        return _denied(UNVERIFIED, "GRANT_BOUND_TO_OTHER_SUPERVISOR", **fields)
    if not liveness(acked_by):
        return _denied(UNVERIFIED, "ACKNOWLEDGING_SUPERVISOR_GONE", **fields)
    return OwnerDecision(status=AUTHORIZED, permitted=True, reasons=(), **fields)


def _record_ack(
    root: Path, supervisor: SupervisorIdentity, *, kind: str, release_sha: str | None,
    now: datetime | None, liveness: Callable[[SupervisorIdentity], bool],
) -> int:
    current = _clock(now)
    key = _owner_key(root)
    if not liveness(supervisor):
        raise OwnerControlError("ACKNOWLEDGING_SUPERVISOR_NOT_LIVE")
    with closing(_connect(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            latest = _latest(db)
            if latest is None:
                raise OwnerControlError("NO_OWNER_COMMAND")
            generation, payload_text, mac = latest
            command = _verified(key, payload_text, mac)
            wanted = TAKE_THE_WHEEL if kind == "GRANT_ACK" else GIVE_ME_CONTROL
            if command.get("action") != wanted:
                raise OwnerControlError("LATEST_COMMAND_IS_NOT_" + wanted)
            if _ack(db, generation) is not None:
                raise OwnerControlError("COMMAND_ALREADY_ACKNOWLEDGED")
            if kind == "GRANT_ACK":
                if current - _parse_time(command.get("issued_at_utc")) > ACK_WINDOW:
                    raise OwnerControlError("GRANT_ACK_WINDOW_EXPIRED")
                if command.get("release_sha") != release_sha:
                    raise OwnerControlError("GRANT_RELEASE_MISMATCH")
            payload = {
                "schema_version": SCHEMA_VERSION,
                "kind": kind,
                "generation": generation,
                "command_id": command["command_id"],
                "release_sha": command.get("release_sha"),
                "supervisor": supervisor.as_dict(),
                "acked_at_utc": current.isoformat(),
            }
            text = _canonical(payload)
            db.execute(
                "INSERT INTO supervisor_acks (generation, payload, mac, recorded_at_utc) VALUES (?,?,?,?)",
                (generation, text, _mac(key, text), current.isoformat()),
            )
            _event(db, kind, generation, {"supervisor": supervisor.as_dict()}, current)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return generation


def acknowledge_grant(
    root: Path, supervisor: SupervisorIdentity, *, release_sha: str, now: datetime | None = None,
    liveness: Callable[[SupervisorIdentity], bool] = supervisor_alive,
) -> OwnerDecision:
    """Bind a fresh, unacknowledged grant to this live supervisor, then decide.

    A grant already bound to another (possibly dead) supervisor is never
    rebound: restarts require a new owner Take the Wheel.
    """
    try:
        _record_ack(root, supervisor, kind="GRANT_ACK", release_sha=release_sha, now=now, liveness=liveness)
    except OwnerControlError:
        pass  # The decision below reports the precise reason.
    except (OSError, sqlite3.Error):
        return _denied(UNVERIFIED, "CONTROLLER_STATE_UNREADABLE")
    return evaluate(root, release_sha=release_sha, supervisor=supervisor, now=now, liveness=liveness)


def acknowledge_revocation(
    root: Path, supervisor: SupervisorIdentity, *, now: datetime | None = None,
    liveness: Callable[[SupervisorIdentity], bool] = supervisor_alive,
) -> bool:
    """Record that this supervisor stopped dispatch for the latest revocation."""
    try:
        _record_ack(root, supervisor, kind="REVOKE_ACK", release_sha=None, now=now, liveness=liveness)
        return True
    except OwnerControlError as exc:
        return exc.code == "COMMAND_ALREADY_ACKNOWLEDGED"
    except (OSError, sqlite3.Error):
        return False


def owner_status(root: Path, *, now: datetime | None = None) -> str:
    """Compatibility status for legacy owner-control consumers.

    AUTHORIZED covers a live acked grant and the short pending-ack window in
    which the supervisor must start and acknowledge; dispatch itself always
    requires the acked decision from :func:`evaluate`.
    """
    decision = evaluate(root, now=now)
    if decision.status in {AUTHORIZED, AUTHORIZED_PENDING_ACK}:
        return AUTHORIZED
    if decision.status == OWNER_CONTROL_ACTIVE:
        return OWNER_CONTROL_ACTIVE
    return "OWNER_CONTROL_UNVERIFIED"


def wait_for_ack(
    root: Path, generation: int, *, timeout_seconds: float, poll_seconds: float = 1.0,
    sleep: Callable[[float], None] | None = None,
) -> str:
    """Return ACKNOWLEDGED, SUPERSEDED or TIMEOUT for a recorded command."""
    import time

    pause = sleep or time.sleep
    deadline = time.monotonic() + timeout_seconds
    while True:
        with closing(_connect_read_only(root)) as db:
            latest = _latest(db)
            if latest is None or latest[0] < generation:
                return "TIMEOUT"
            if latest[0] > generation:
                return "SUPERSEDED"
            if _ack(db, generation) is not None:
                return "ACKNOWLEDGED"
        if time.monotonic() >= deadline:
            return "TIMEOUT"
        pause(poll_seconds)


def recent_events(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    with closing(_connect_read_only(root)) as db:
        rows = db.execute(
            "SELECT at_utc, kind, generation, detail FROM control_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{"at_utc": a, "kind": k, "generation": g, "detail": json.loads(d)} for a, k, g, d in rows]


# --- CLI used by the desktop controls ------------------------------------------

def _pin_sha(pin_path: str | None) -> str | None:
    if not pin_path:
        return None
    try:
        sha = json.loads(Path(pin_path).read_text(encoding="utf-8-sig")).get("accepted_sha")
    except (OSError, ValueError, AttributeError):
        return None
    return sha if isinstance(sha, str) and _SHA.fullmatch(sha) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iron God authoritative owner controller")
    parser.add_argument("--root", help="control directory (default: under LOCALAPPDATA)")
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue")
    issue.add_argument("--action", required=True, choices=sorted(ACTIONS))
    issue.add_argument("--pin", help="supervisor-pinned-release.json")
    status = sub.add_parser("status")
    status.add_argument("--release-sha")
    wait = sub.add_parser("wait-ack")
    wait.add_argument("--generation", type=int, required=True)
    wait.add_argument("--timeout", type=float, default=120.0)
    sub.add_parser("events")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else control_root()
    try:
        if args.command == "issue":
            release = _pin_sha(args.pin)
            if args.action == TAKE_THE_WHEEL and release is None:
                raise OwnerControlError("ACCEPTED_RELEASE_PIN_UNREADABLE")
            print(json.dumps(issue_command(root, args.action, release_sha=release), sort_keys=True))
            return 0
        if args.command == "status":
            decision = evaluate(root, release_sha=args.release_sha)
            print(json.dumps(decision.as_dict(), sort_keys=True))
            return 0 if decision.permitted else 1
        if args.command == "wait-ack":
            result = wait_for_ack(root, args.generation, timeout_seconds=args.timeout)
            print(json.dumps({"generation": args.generation, "result": result}))
            return {"ACKNOWLEDGED": 0, "SUPERSEDED": 7}.get(result, 6)
        print(json.dumps(recent_events(root), sort_keys=True))
        return 0
    except OwnerControlError as exc:
        print(json.dumps({"status": "REFUSED", "reason": exc.code}))
        return 3 if exc.code in {"NOT_ACTIVE_CONSOLE_SESSION", "NOT_DESKTOP_CONTROL_ORIGIN"} else 4


if __name__ == "__main__":
    sys.exit(main())
