from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
import re
import subprocess
import sys
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DATADOG_SPEC = importlib.util.spec_from_file_location(
    "datadog_actor_metrics", Path(__file__).with_name("datadog_actor_metrics.py")
)
if _DATADOG_SPEC is None or _DATADOG_SPEC.loader is None:
    raise RuntimeError("unable to load Datadog actor metrics adapter")
_DATADOG_MODULE = importlib.util.module_from_spec(_DATADOG_SPEC)
_DATADOG_SPEC.loader.exec_module(_DATADOG_MODULE)
emit_datadog_metrics = _DATADOG_MODULE.emit

_STEWARD_SPEC = importlib.util.spec_from_file_location(
    "project_orchestrator_steward",
    Path(__file__).resolve().parents[1] / "src" / "project_orchestrator" / "steward.py",
)
if _STEWARD_SPEC is None or _STEWARD_SPEC.loader is None:
    raise RuntimeError("unable to load Steward machine-health governor")
_STEWARD_MODULE = importlib.util.module_from_spec(_STEWARD_SPEC)
_STEWARD_SPEC.loader.exec_module(_STEWARD_MODULE)
assess_machine_health = _STEWARD_MODULE.assess_machine_health


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def windows_idle_seconds() -> float | None:
    """Return interactive Windows idle time, or None off Windows."""
    if os.name != "nt":
        return None

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise RuntimeError("GetLastInputInfo failed")
    tick = ctypes.windll.kernel32.GetTickCount64()
    return max(0.0, (tick - info.dwTime) / 1000.0)


def update_actor_checkout(actor_repo: Path) -> str:
    """Update the dedicated actor only from a verified accepted LOCAL commit.

    Missing pins never enable a stale GitHub fallback; existing actor work is
    never discarded by the release replacement routine.
    """
    if not (actor_repo / ".git").is_dir():
        raise RuntimeError("actor repository is not a Git checkout")
    # Local-first promotion is opt-in and pinned to an immutable commit.
    # Never infer permission to deploy from a mutable development branch.
    local_repo = os.environ.get("IRON_GOD_ACCEPTED_REPO")
    accepted_head = os.environ.get("IRON_GOD_ACCEPTED_SHA")
    if bool(local_repo) != bool(accepted_head):
        raise RuntimeError("local actor promotion requires both repository and exact SHA")
    if not local_repo:
        raise RuntimeError("LOCAL_ACCEPTED_RELEASE_REQUIRED: refusing GitHub origin/main fallback")
    if re.fullmatch(r"[0-9a-f]{40}", accepted_head) is None:
        raise RuntimeError("LOCAL_ACCEPTED_SHA_INVALID")
    if not Path(local_repo).is_absolute():
        raise RuntimeError("LOCAL_ACCEPTED_SOURCE_NOT_ABSOLUTE")
    if local_repo:
        source = Path(local_repo).resolve()
        actor = actor_repo.resolve()
        if source == actor or source in actor.parents or actor in source.parents:
            raise RuntimeError("LOCAL_ACTOR_SOURCE_OVERLAP")
        if not (source / ".git").exists():
            raise RuntimeError("accepted local source is not a Git repository")
        head = run(["git", "rev-parse", "HEAD"], source)
        clean = run(["git", "status", "--porcelain", "--untracked-files=normal"], source)
        if head.returncode != 0 or head.stdout.strip() != accepted_head or clean.returncode != 0 or clean.stdout.strip():
            raise RuntimeError("accepted local source does not match clean exact HEAD")
        fetch = run(["git", "fetch", "--no-tags", str(source), accepted_head], actor_repo)
        remote = run(["git", "rev-parse", "FETCH_HEAD"], actor_repo)
    if fetch.returncode != 0 or remote.returncode != 0:
        raise RuntimeError("actor checkout source fetch failed")
    target = remote.stdout.strip()
    if target != accepted_head:
        raise RuntimeError("LOCAL_FETCHED_HEAD_MISMATCH")
    current = run(["git", "rev-parse", "HEAD"], actor_repo)
    if current.returncode != 0 or len(current.stdout.strip()) != 40:
        raise RuntimeError("actor checkout current HEAD is unverified")
    current_sha = current.stdout.strip()
    # Never destroy unique actor-checkout changes, even when the checkout is
    # nominally disposable. The old post-reset cleanliness check came too late.
    clean_before = run(["git", "status", "--porcelain"], actor_repo)
    if clean_before.returncode != 0 or clean_before.stdout.strip():
        raise RuntimeError("actor checkout is dirty; refusing release replacement")
    if current_sha == target:
        return target
    reset = run(["git", "reset", "--hard", target], actor_repo)
    if reset.returncode != 0:
        raise RuntimeError("actor checkout reset failed")
    final_head = run(["git", "rev-parse", "HEAD"], actor_repo)
    clean = run(["git", "status", "--porcelain"], actor_repo)
    if final_head.returncode != 0 or final_head.stdout.strip() != accepted_head:
        raise RuntimeError("LOCAL_ACTOR_POST_RESET_HEAD_MISMATCH")
    if clean.returncode != 0 or clean.stdout.strip():
        raise RuntimeError("actor checkout is not clean after update")
    return target


def execute_bridge_once(actor_repo: Path, queue: Path, workspace_root: Path) -> int:
    bridge = actor_repo / "scripts" / "laptop_actor_github_bridge.py"
    runner = actor_repo / "scripts" / "laptop_actor_runner.py"
    if not bridge.is_file() or not runner.is_file():
        raise RuntimeError("actor checkout is missing bridge or runner")
    proc = subprocess.run(
        [
            sys.executable,
            str(bridge),
            "--queue",
            str(queue),
            "--workspace-root",
            str(workspace_root),
            "--runner",
            str(runner),
            "--once",
        ],
        cwd=actor_repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode


def emit_state(state_dir: Path, state: str, **fields: object) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "state": state,
        **fields,
    }
    path = state_dir / "actor-telemetry.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    emit_datadog_metrics(payload)


def acquire_single_instance(state_dir: Path) -> int | None:
    """Kernel-held singleton; a crashed process cannot strand an O_EXCL PID file.

    The file itself is persistent, including when upgrading from the old
    create-exclusive PID-file implementation. Only the OS lock is authority.
    """
    import errno

    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / "actor.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
            os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                os.close(fd)
                return None
            raise
        # Update informational PID only while owning the kernel lock.
        # PID recycling or a stale PID from an earlier crash cannot veto work.
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.fsync(fd)
        return fd
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def release_single_instance(state_dir: Path, fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    # Never unlink the lock: that could allow a second inode/owner race.


def _windows_cpu_percent(sample_seconds: float = 0.1) -> float | None:
    if os.name != "nt":
        return None

    class FILETIME(ctypes.Structure):
        _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

    def read_times() -> tuple[int, int, int] | None:
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None

        def ticks(value: FILETIME) -> int:
            return (value.high << 32) | value.low

        return ticks(idle), ticks(kernel), ticks(user)

    first = read_times()
    if first is None:
        return None
    ctypes.windll.kernel32.Sleep(max(10, int(sample_seconds * 1000)))
    second = read_times()
    if second is None:
        return None
    idle_delta = second[0] - first[0]
    total_delta = (second[1] - first[1]) + (second[2] - first[2])
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)


def _windows_fixed_disks() -> list[dict[str, object]]:
    if os.name != "nt":
        disk = shutil.disk_usage(Path.home())
        return [
            {
                "name": str(Path.home().anchor or Path.home()),
                "used_percent": round(100.0 * (1.0 - disk.free / disk.total), 1),
                "free_gb": round(disk.free / (1024**3), 2),
                "total_gb": round(disk.total / (1024**3), 2),
            }
        ]
    kernel = ctypes.windll.kernel32
    mask = int(kernel.GetLogicalDrives())
    disks: list[dict[str, object]] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = f"{chr(65 + index)}:\\"
        if int(kernel.GetDriveTypeW(root)) != 3:
            continue
        free = ctypes.c_ulonglong()
        total = ctypes.c_ulonglong()
        total_free = ctypes.c_ulonglong()
        if not kernel.GetDiskFreeSpaceExW(
            root, ctypes.byref(free), ctypes.byref(total), ctypes.byref(total_free)
        ):
            continue
        if total.value <= 0:
            continue
        disks.append(
            {
                "name": root[:2],
                "used_percent": round(100.0 * (1.0 - free.value / total.value), 1),
                "free_gb": round(free.value / (1024**3), 2),
                "total_gb": round(total.value / (1024**3), 2),
            }
        )
    return disks


def _windows_power_status() -> tuple[bool | None, float | None]:
    if os.name != "nt":
        return None, None

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_byte),
            ("BatteryFlag", ctypes.c_byte),
            ("BatteryLifePercent", ctypes.c_byte),
            ("SystemStatusFlag", ctypes.c_byte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None, None
    on_ac = None if status.ACLineStatus == 255 else status.ACLineStatus == 1
    battery = (
        None if status.BatteryLifePercent == 255 else float(status.BatteryLifePercent)
    )
    return on_ac, battery


def _windows_heavy_processes() -> list[str]:
    if os.name != "nt":
        return []
    proc = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        lowered = line.lower()
        for needle in ("ollama", "llama", "unreal", "ue4", "ue5", "aider"):
            if needle in lowered:
                names.add(needle)
    return sorted(names)


def system_resource_snapshot() -> dict[str, object]:
    memory_percent: float | None = None
    memory_available_gb: float | None = None
    memory_total_gb: float | None = None
    commit_used_percent: float | None = None
    if os.name == "nt":

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            memory_percent = float(status.dwMemoryLoad)
            memory_available_gb = round(status.ullAvailPhys / (1024**3), 2)
            memory_total_gb = round(status.ullTotalPhys / (1024**3), 2)
            if status.ullTotalPageFile:
                commit_used_percent = round(
                    100.0 * (1.0 - status.ullAvailPageFile / status.ullTotalPageFile), 1
                )
    on_ac_power, battery_percent = _windows_power_status()
    disks = _windows_fixed_disks()
    snapshot: dict[str, object] = {
        "memory_percent": memory_percent,
        "memory_available_gb": memory_available_gb,
        "memory_total_gb": memory_total_gb,
        "commit_used_percent": commit_used_percent,
        "cpu_percent": _windows_cpu_percent(),
        "temperature_c": None,
        "temperature_source": "UNAVAILABLE",
        "on_ac_power": on_ac_power,
        "battery_percent": battery_percent,
        "active_heavy_processes": _windows_heavy_processes(),
        "disks": disks,
    }
    if disks:
        snapshot["disk_free_gb"] = min(float(item["free_gb"]) for item in disks)
        snapshot["disk_used_percent"] = max(
            float(item["used_percent"]) for item in disks
        )
    return snapshot


def steward_maintenance_active() -> tuple[bool, str | None]:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return False, None
    marker = Path(base) / "ExponentialEngine" / "steward" / "maintenance.json"
    if not marker.is_file():
        return False, None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, TypeError):
        return True, "MAINTENANCE_MARKER_UNREADABLE"
    return True, str(payload.get("status") or "MAINTENANCE")


def run_daily_hygiene(
    state_dir: Path,
    *,
    retention_days: int = 5,
    minimum_interval_hours: int = 20,
    max_jsonl_bytes: int = 10 * 1024 * 1024,
    now: datetime | None = None,
) -> dict[str, object]:
    """Perform bounded cleanup only inside known automation-owned state/log roots."""
    current = now or datetime.now(timezone.utc)
    report_path = state_dir / "steward-hygiene.json"
    try:
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        completed = datetime.fromisoformat(str(previous["completed_at_utc"]))
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        if current - completed < timedelta(hours=max(1, minimum_interval_hours)):
            return {"status": "SKIPPED_RECENT", "completed_at_utc": completed.isoformat()}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass

    base = Path(os.environ.get("LOCALAPPDATA", state_dir.parent))
    roots = [
        base / "ExponentialEngine" / "logs",
        base / "PortfolioIdleWorker" / "logs",
        base / "ExponentialEngine" / "flow-state",
        state_dir,
    ]
    cutoff = current - timedelta(days=max(1, retention_days))
    removed: list[str] = []
    errors: list[str] = []

    for root in roots:
        if not root.is_dir():
            continue
        for item in root.rglob("*"):
            if not item.is_file() or item.suffix.lower() not in {".log", ".jsonl", ".tmp"}:
                continue
            if item == state_dir / "actor-telemetry.jsonl":
                continue
            try:
                modified = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                if modified >= cutoff:
                    continue
                item.unlink()
                removed.append(str(item))
            except OSError as exc:
                errors.append(f"{item}:{type(exc).__name__}")

    rotated: str | None = None
    telemetry = state_dir / "actor-telemetry.jsonl"
    try:
        if telemetry.is_file() and telemetry.stat().st_size > max_jsonl_bytes:
            stamp = current.strftime("%Y%m%dT%H%M%SZ")
            destination = telemetry.with_name(f"actor-telemetry-{stamp}.jsonl")
            telemetry.replace(destination)
            rotated = str(destination)
    except OSError as exc:
        errors.append(f"{telemetry}:{type(exc).__name__}")

    report = {
        "status": "COMPLETED_WITH_ERRORS" if errors else "COMPLETED",
        "completed_at_utc": current.isoformat(),
        "retention_days": max(1, retention_days),
        "removed_count": len(removed),
        "removed_files": removed,
        "rotated_telemetry": rotated,
        "errors": errors,
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def resources_allow_execution(
    snapshot: dict[str, object],
    *,
    max_memory_percent: float = 85.0,
    min_disk_free_gb: float = 10.0,
) -> bool:
    """Legacy hard-cap helper retained for compatibility; runtime uses Steward."""
    memory = snapshot.get("memory_percent")
    disk = snapshot.get("disk_free_gb")
    if isinstance(memory, (int, float)) and float(memory) > max_memory_percent:
        return False
    return isinstance(disk, (int, float)) and float(disk) >= min_disk_free_gb


def run_idle_session(
    actor_repo: Path,
    queue: Path,
    workspace_root: Path,
    min_idle_seconds: int,
    poll_seconds: int,
    state_dir: Path | None = None,
    max_backoff_seconds: int = 900,
    max_memory_percent: float = 85.0,
    min_disk_free_gb: float = 10.0,
) -> int:
    """Run bounded actor cycles while the Windows session remains idle.

    The first cycle is allowed only after the configured idle threshold. After that,
    the actor checks for newly queued work at a short cadence and yields as soon as
    keyboard/mouse activity drops the idle time below the threshold.
    """
    threshold = max(0, min_idle_seconds)
    cadence = max(30, poll_seconds)
    state_dir = state_dir or (queue / "state")
    lock_fd = acquire_single_instance(state_dir)
    if lock_fd is None:
        return 0
    idle = windows_idle_seconds()
    if idle is not None and idle < threshold:
        emit_state(state_dir, "USER_ACTIVE", idle_seconds=idle)
        release_single_instance(state_dir, lock_fd)
        return 0

    failures = 0
    try:
        maintenance, maintenance_status = steward_maintenance_active()
        if maintenance:
            emit_state(
                state_dir,
                "COOLDOWN",
                reason="steward_maintenance",
                maintenance_status=maintenance_status,
            )
            return 0
        emit_state(state_dir, "IDLE_AVAILABLE", idle_seconds=idle)
        update_actor_checkout(actor_repo)
        run_daily_hygiene(state_dir)
        while True:
            maintenance, maintenance_status = steward_maintenance_active()
            if maintenance:
                emit_state(
                    state_dir,
                    "COOLDOWN",
                    reason="steward_maintenance",
                    maintenance_status=maintenance_status,
                )
                return 0
            idle = windows_idle_seconds()
            if idle is not None and idle < threshold:
                emit_state(state_dir, "USER_ACTIVE", idle_seconds=idle)
                return 0
            resources = system_resource_snapshot()
            assessment = assess_machine_health(resources)
            if not assessment["allow_new_engineering"]:
                emit_state(
                    state_dir,
                    "COOLDOWN",
                    reason="steward_machine_health",
                    steward_band=assessment["band"],
                    steward_reasons=assessment["reasons"],
                    steward_recovery_actions=assessment["recovery_actions"],
                    **resources,
                )
                time.sleep(cadence)
                continue
            started = time.monotonic()
            emit_state(
                state_dir,
                "EXECUTING",
                idle_seconds=idle,
                steward_band=assessment["band"],
                steward_max_parallel_lanes=assessment["max_parallel_lanes"],
                **resources,
            )
            rc = execute_bridge_once(actor_repo, queue, workspace_root)
            if rc == 0:
                failures = 0
                emit_state(
                    state_dir,
                    "IDLE_AVAILABLE",
                    result="bridge_cycle_green",
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                time.sleep(cadence)
                continue
            failures += 1
            backoff = min(max_backoff_seconds, cadence * (2 ** min(failures - 1, 4)))
            emit_state(
                state_dir,
                "COOLDOWN",
                returncode=rc,
                backoff_seconds=backoff,
                retry_count=failures,
                duration_seconds=round(time.monotonic() - started, 3),
            )
            time.sleep(backoff)
    finally:
        release_single_instance(state_dir, lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Self-updating Exponential Engine laptop actor bootstrap"
    )
    parser.add_argument("--actor-repo", required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--min-idle-seconds", type=int, default=300)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--state-dir")
    parser.add_argument("--max-backoff-seconds", type=int, default=900)
    parser.add_argument("--max-memory-percent", type=float, default=85.0)
    parser.add_argument("--min-disk-free-gb", type=float, default=10.0)
    args = parser.parse_args()
    actor_repo = Path(args.actor_repo)
    queue = Path(args.queue)
    workspace_root = Path(args.workspace_root)
    try:
        return run_idle_session(
            actor_repo,
            queue,
            workspace_root,
            args.min_idle_seconds,
            args.poll_seconds,
            Path(args.state_dir) if args.state_dir else None,
            args.max_backoff_seconds,
            args.max_memory_percent,
            args.min_disk_free_gb,
        )
    except (OSError, RuntimeError):
        # Fail closed. A bootstrap/update failure must never manufacture worker
        # evidence or mutate any product repository.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
