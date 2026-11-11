"""Independent low-overhead flight recorder for freeze diagnosis on the owner's laptop (read-only, advisory).

Every few seconds it appends one JSON line with machine-wide CPU, memory, commit and free disk, the heaviest processes, and the
state of the processes that matter in a freeze (Ollama / llama-server, Python workers, Norton).  Each line is flushed and
fsynced, so after a hard reboot the last line shows what the machine was doing in the seconds before it stopped.

It never talks to the engine, never changes a setting and never launches a model.  It is a separate process on purpose: the
supervisor's own 30-second log can stop at the same moment as the thing it is logging.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_INTERVAL_S = 5.0
MAX_BYTES = 8 * 1024 * 1024          # per file
KEEP_FILES = 4                       # recorder.jsonl plus three rotated files: about 32 MB at most
TOP_N = 5
WATCHED = ("ollama", "llama-server", "llama_server", "python", "pythonw", "powershell", "pwsh", "cmd", "conhost",
           "norton", "nortonsecurity", "nsservice", "ccsvchst", "aswengsrv", "aswidsagent", "nswscsvc", "msmpeng")


def default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "ExponentialEngine" / "flight-recorder" / "recorder.jsonl"


def _watched(name: str) -> bool:
    lowered = name.lower().removesuffix(".exe")
    return any(lowered == w or lowered.startswith(w) for w in WATCHED)


class Recorder:
    """Keeps process handles between samples so per-process CPU percentages are real, not zero."""

    def __init__(self, psutil_module: Any = None, disk_root: str | None = None) -> None:
        if psutil_module is None:
            import psutil as psutil_module  # optional dependency of the engine environment
        self.ps = psutil_module
        self.disk_root = disk_root or (os.environ.get("SystemDrive", "C:") + "\\")
        self.handles: dict[int, Any] = {}
        self.ps.cpu_percent(None)

    def _procs(self) -> list[dict[str, Any]]:
        seen: set[int] = set()
        rows: list[dict[str, Any]] = []
        for proc in self.ps.process_iter(["pid", "name"]):
            try:
                pid = int(proc.info["pid"])
                seen.add(pid)
                handle = self.handles.setdefault(pid, proc)
                cpu = float(handle.cpu_percent(None))
                rss = int(handle.memory_info().rss)
                rows.append({"pid": pid, "name": str(proc.info["name"] or ""), "cpu": round(cpu, 1), "rss_mb": rss // (1024 * 1024)})
            except Exception:  # noqa: BLE001 - a process that exits or is denied must never stop the recorder
                continue
        for gone in set(self.handles) - seen:
            self.handles.pop(gone, None)
        return rows

    def sample(self, now: float | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        memory = self.ps.virtual_memory()
        rows = self._procs()
        disk = shutil.disk_usage(self.disk_root)
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc).isoformat(timespec="seconds"),
            "cpu_pct": round(float(self.ps.cpu_percent(None)), 1),
            "mem_avail_mb": int(memory.available) // (1024 * 1024),
            "mem_used_pct": round(float(memory.percent), 1),
            "disk_free_gb": round(disk.free / 1024**3, 1),
            "top_cpu": sorted(rows, key=lambda r: r["cpu"], reverse=True)[:TOP_N],
            "top_rss": sorted(rows, key=lambda r: r["rss_mb"], reverse=True)[:TOP_N],
            "watched": sorted((r for r in rows if _watched(r["name"])), key=lambda r: (r["name"].lower(), r["pid"])),
        }
        try:
            swap = self.ps.swap_memory()
            entry["swap_used_pct"] = round(float(swap.percent), 1)
        except Exception:  # noqa: BLE001
            pass
        entry["sample_ms"] = int((time.perf_counter() - started) * 1000)
        return entry


def rotate(path: Path, max_bytes: int = MAX_BYTES, keep: int = KEEP_FILES) -> None:
    """recorder.jsonl -> recorder.jsonl.1 -> ... ; the oldest file beyond ``keep`` is dropped (this is its own data only)."""
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
    except OSError:
        return
    oldest = path.with_name(f"{path.name}.{keep - 1}")
    if oldest.exists():
        oldest.unlink()
    for index in range(keep - 2, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def append(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run(path: Path | None = None, interval_s: float = DEFAULT_INTERVAL_S, *, recorder: Recorder | None = None,
        should_stop: Callable[[], bool] | None = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> int:
    """Sample forever (or until ``should_stop``); returns the number of lines written. Holds no engine state."""
    target = path or default_path()
    recorder = recorder or Recorder()
    written = 0
    while not (should_stop and should_stop()):
        begun = clock()
        try:
            append(target, recorder.sample(begun))
            written += 1
        except OSError:
            pass  # a full or locked disk is the very thing being investigated; keep trying on the next beat
        sleep(max(0.5, interval_s - (clock() - begun)))
    return written
