"""Evidence that the builder model is unloaded while the heavy gates run (issue #594).

On the 16 GB host the resident 7B model (about 5 GB) overlapped mypy and the canonical preflight
(2026-10-31: 404 MB free). The worker now unloads it first (``release_builder_model``), but an earlier
watcher matched the wrong processes and proved nothing. This one identifies each gate by its command
line, samples Ollama's ``/api/ps`` and free RAM while a gate is running, and reports per gate:

* ``VERIFIED_UNLOADED``  - at least ``MIN_SAMPLES`` samples, the builder model resident in none;
* ``MODEL_RESIDENT``     - the model was resident in at least one sample while the gate ran;
* ``INSUFFICIENT_SAMPLES`` - the gate was seen, but too briefly to say;
* ``NOT_OBSERVED``       - no such gate ran during the watch (no claim is made).

The watcher only reads: it never starts, stops or unloads anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

MIN_SAMPLES = 3
GATES = ("mypy", "preflight")
PREFLIGHT_RE = re.compile(r"(^|[\\/])engineering[\\/]preflight\.py$")


def classify_gate(command_line: str) -> str | None:
    """'mypy' or 'preflight' when the process RUNS that gate; a shell, editor or git that merely names it is not one."""
    tokens = command_line.replace('"', " ").split()
    if not tokens:
        return None
    program = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if program in {"mypy", "mypy.exe"}:
        return "mypy"
    if not program.startswith("python"):
        return None
    arguments = tokens[1:]
    if arguments[:1] == ["-m"] or (len(arguments) > 1 and arguments[0].startswith("-") and "-m" in arguments[:3]):
        flags = arguments.index("-m")
        return "mypy" if arguments[flags + 1:flags + 2] == ["mypy"] else None
    script = next((token for token in arguments if not token.startswith("-")), "")
    return "preflight" if PREFLIGHT_RE.search(script) else None


def builder_name() -> str:
    from .iron_god_local_model import default_ollama_model

    return default_ollama_model().split("/", 1)[-1]


def resident_models(base: str | None = None, timeout: float = 3.0) -> list[str] | None:
    """Names Ollama reports as loaded, or None when Ollama cannot be asked (never treated as 'unloaded')."""
    url = (base or os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434")).rstrip("/") + "/api/ps"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return [str(row.get("name") or row.get("model")) for row in json.load(response).get("models", [])]
    except (OSError, ValueError):
        return None


def free_ram_mb() -> float:
    command = "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=30)
    return float(out.stdout.strip())


def running_commands() -> list[str]:
    command = "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=60)
    return [line for line in out.stdout.splitlines() if line.strip()]


def watch(duration: float, interval: float, *, model: str | None = None,
          commands: Callable[[], list[str]] = running_commands, models: Callable[[], list[str] | None] = resident_models,
          free_mb: Callable[[], float] = free_ram_mb, clock: Callable[[], float] = time.monotonic,
          sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Sample until ``duration`` elapses; returns the per-gate evidence."""
    wanted = (model or builder_name()).lower()
    gates: dict[str, dict[str, Any]] = {g: {"samples": 0, "model_resident_samples": 0, "ollama_unreadable_samples": 0,
                                           "min_free_mb": None} for g in GATES}
    overall_min: float | None = None
    samples = 0
    end = clock() + duration
    while clock() < end:
        free = free_mb()
        overall_min = free if overall_min is None else min(overall_min, free)
        active = {g for g in map(classify_gate, commands()) if g}
        loaded = models() if active else []
        for gate in active:
            row = gates[gate]
            row["samples"] += 1
            row["min_free_mb"] = free if row["min_free_mb"] is None else min(row["min_free_mb"], free)
            if loaded is None:
                row["ollama_unreadable_samples"] += 1
            elif any(wanted in name.lower() for name in loaded):
                row["model_resident_samples"] += 1
        samples += 1
        sleep(interval)
    for row in gates.values():
        if row["samples"] == 0:
            row["verdict"] = "NOT_OBSERVED"
        elif row["model_resident_samples"]:
            row["verdict"] = "MODEL_RESIDENT"
        elif row["ollama_unreadable_samples"] or row["samples"] < MIN_SAMPLES:
            row["verdict"] = "INSUFFICIENT_SAMPLES"
        else:
            row["verdict"] = "VERIFIED_UNLOADED"
    return {"schema_version": 1, "builder_model": wanted, "samples": samples, "min_free_mb": overall_min, "gates": gates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observe whether the builder model is unloaded during mypy/preflight.")
    parser.add_argument("--seconds", type=float, default=1500)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = watch(args.seconds, args.interval)
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
