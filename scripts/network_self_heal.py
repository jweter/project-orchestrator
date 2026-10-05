"""Bounded network self-healing for the Azathoth hands-off runtime."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HOSTS = ("1.1.1.1", "8.8.8.8")
DNS_PROBE_HOST = "github.com"
REBOOT_LATCH = "network-reboot-cooldown.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def internet_ok(timeout: float = 2.0) -> bool:
    try:
        socket.getaddrinfo(DNS_PROBE_HOST, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for host in HOSTS:
        try:
            with socket.create_connection((host, 443), timeout=timeout):
                return True
        except OSError:
            pass
    return False


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )


def human_control_roots(state_root: Path, human_control_root: Path | None = None) -> tuple[Path, ...]:
    roots = [state_root]
    if human_control_root is not None and human_control_root not in roots:
        roots.append(human_control_root)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        canonical = Path(local_app_data) / "ExponentialEngine" / "flow-state"
        if canonical not in roots:
            roots.append(canonical)
    return tuple(roots)


def human_mode(state_root: Path, human_control_root: Path | None = None) -> bool:
    markers = ("human-mode.json", "give-me-control")
    return any(
        (root / marker).exists()
        for root in human_control_roots(state_root, human_control_root)
        for marker in markers
    )


def log(state_root: Path, event: str, **details: object) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    record = {"ts": now(), "event": event, **details}
    with (state_root / "network-recovery.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def reboot_latched(state_root: Path) -> bool:
    return (state_root / REBOOT_LATCH).exists()


def set_reboot_latch(state_root: Path) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / REBOOT_LATCH).write_text(
        json.dumps({"requested_at_utc": now(), "reason": "NETWORK_RECOVERY_EXHAUSTED"}, indent=2),
        encoding="utf-8",
    )


def clear_reboot_latch(state_root: Path, reason: str) -> None:
    latch = state_root / REBOOT_LATCH
    if latch.exists():
        latch.unlink()
        log(state_root, "REBOOT_COOLDOWN_CLEARED", reason=reason)


def snapshot(state_root: Path) -> None:
    evidence: dict[str, object] = {}
    commands = {
        "wlan": ["netsh", "wlan", "show", "interfaces"],
        "ip": ["ipconfig", "/all"],
        "route": ["route", "print", "-4"],
    }
    for name, command in commands.items():
        try:
            result = run(command)
            evidence[name] = {
                "rc": result.returncode,
                "stdout": result.stdout[-12000:],
                "stderr": result.stderr[-2000:],
            }
        except Exception as exc:
            evidence[name] = {"error": repr(exc)}
    incident_root = state_root / "network-incidents"
    incident_root.mkdir(parents=True, exist_ok=True)
    path = incident_root / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"ts": now(), **evidence}, indent=2), encoding="utf-8")


def recover(
    state_root: Path,
    allow_reboot: bool = False,
    human_control_root: Path | None = None,
) -> bool:
    snapshot(state_root)
    if human_mode(state_root, human_control_root):
        log(state_root, "RECOVERY_SUPPRESSED_HUMAN_MODE")
        return False

    steps = [
        ("wlan_reconnect", ["netsh", "wlan", "connect", "name=ATTgw6SIjk"]),
        (
            "wlan_service_restart",
            ["powershell", "-NoProfile", "-Command", "Restart-Service WlanSvc -Force"],
        ),
        (
            "adapter_restart",
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-NetAdapter -InterfaceDescription 'Intel(R) Wi-Fi 6E AX211 160MHz' | "
                "Restart-NetAdapter -Confirm:$false",
            ],
        ),
    ]
    for name, command in steps:
        try:
            result = run(command)
            log(state_root, name, rc=result.returncode, stderr=result.stderr[-1000:])
        except Exception as exc:
            log(state_root, name, error=repr(exc))
        time.sleep(8)
        if internet_ok():
            clear_reboot_latch(state_root, "CONNECTIVITY_RECOVERED")
            log(state_root, "RECOVERED", step=name)
            return True

    log(state_root, "RECOVERY_EXHAUSTED")
    if allow_reboot and not human_mode(state_root, human_control_root):
        if reboot_latched(state_root):
            log(state_root, "REBOOT_SUPPRESSED_COOLDOWN")
        else:
            set_reboot_latch(state_root)
            log(state_root, "BOUNDED_REBOOT_REQUESTED")
            result = run(
                [
                    "shutdown",
                    "/r",
                    "/t",
                    "15",
                    "/c",
                    "Azathoth network recovery exhausted; rebooting to restore connectivity",
                ]
            )
            if result.returncode == 0:
                for _ in range(15):
                    time.sleep(1)
                    if human_mode(state_root, human_control_root):
                        run(["shutdown", "/a"])
                        log(state_root, "REBOOT_ABORTED_HUMAN_MODE")
                        return False
                    if internet_ok():
                        run(["shutdown", "/a"])
                        clear_reboot_latch(state_root, "CONNECTIVITY_RECOVERED_DURING_REBOOT_COUNTDOWN")
                        log(state_root, "REBOOT_ABORTED_CONNECTIVITY_RECOVERED")
                        return True
    return False


def acquire_guardian(state_root: Path):
    """Acquire an OS-released singleton lock; return an open handle or None."""
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / "network-guardian.lock"
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        handle.close()
        return None

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode("ascii"))
    handle.flush()
    return handle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--failures", type=int, default=3)
    parser.add_argument("--allow-reboot", action="store_true")
    parser.add_argument("--human-control-root", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reset-reboot-cooldown", action="store_true")
    args = parser.parse_args()

    if args.reset_reboot_cooldown:
        clear_reboot_latch(args.state_root, "HUMAN_RESET")
        return 0

    guardian_lock = acquire_guardian(args.state_root)
    if guardian_lock is None:
        return 0

    failures = 0
    try:
        log(args.state_root, "GUARDIAN_STARTED", pid=os.getpid())
        while True:
            healthy = internet_ok()
            if healthy:
                failures = 0
                clear_reboot_latch(args.state_root, "CONNECTIVITY_CONFIRMED")
            else:
                failures += 1
                log(args.state_root, "PROBE_FAILED", consecutive=failures)

            if failures >= args.failures:
                recover(args.state_root, args.allow_reboot, args.human_control_root)
                failures = 0

            if args.once:
                break
            time.sleep(max(10, args.poll_seconds))
    finally:
        guardian_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
