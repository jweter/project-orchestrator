"""Isolated, zero-fee local coding-agent qualification harness.

No installed Iron God runtime, owner-control state, or production repository is changed.
Tool-using agents are inventory-only until a separate OS sandbox is established.
"""

from __future__ import annotations

import argparse
import ctypes
import csv
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import urllib.request

CANDIDATES = (
    ("restricted-patch", "builtin", "Local Ollama JSON -> allowlisted file writer"),
    ("aider", "aider", "Existing local baseline; tool-using"),
    ("pi", "pi", "Tool-using; isolated installation"),
    ("mini-swe-agent", "mini", "Shell-using; needs OS sandbox"),
    ("codex", "codex", "Tool-using; local Ollama only"),
    ("opencode", "opencode", "Tool-using; local Ollama only"),
    ("kilo", "kilo", "Tool-using; local Ollama only"),
    ("goose", "goose", "Tool-using; local Ollama only"),
    ("openhands", "openhands", "Requires separate WSL/Docker isolation"),
    ("claude-code-local", "claude", "Local Ollama provider; separate config"),
)
ALLOWED = frozenset({"src/tempo.py"})
TASK = """Implement snap_beats(seconds, bpm) in src/tempo.py.
Return a list of times snapped to the nearest nonnegative whole beat interval
(60 / bpm). Preserve the input sequence order. Exact halfway cases round
toward the later beat; negative input times clamp to zero. If bpm is not a
positive finite number, raise ValueError. Reject non-finite time values with
ValueError. Do not modify tests or create other files. Python standard
library only. Return valid JSON: {"files":{"src/tempo.py":"full Python source"}}.
"""
BASELINE = '"""Snap event timestamps to a tempo grid."""\n\ndef snap_beats(seconds, bpm):\n    raise NotImplementedError\n'
TESTS = r"""import math
import unittest
from src.tempo import snap_beats

class TestSnapBeats(unittest.TestCase):
    def test_grid(self):
        self.assertEqual(snap_beats([0, .72, 1.44, 2.16], 120), [0, .5, 1.5, 2.0])

    def test_halfway_goes_forward(self):
        self.assertEqual(snap_beats([.25, .75, 1.25], 120), [.5, 1.0, 1.5])

    def test_clamp_and_order(self):
        self.assertEqual(snap_beats([1.0, -1.0, 0.0], 120), [1.0, 0.0, 0.0])

    def test_invalid_tempo(self):
        for bpm in [0, -2, float("nan"), float("inf")]:
            with self.subTest(bpm=bpm), self.assertRaises(ValueError):
                snap_beats([0], bpm)

    def test_invalid_time(self):
        for t in [float("nan"), float("inf")]:
            with self.subTest(t=t), self.assertRaises(ValueError):
                snap_beats([t], 120)

if __name__ == "__main__":
    unittest.main()
"""


def memory_gib() -> float:
    if os.name != "nt":
        return 0.0

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

    state = MEMORYSTATUSEX()
    state.dwLength = ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise OSError("GlobalMemoryStatusEx failed")
    return round(state.ullAvailPhys / (1024**3), 2)


def live_supervisor() -> bool:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        return False
    file = (
        Path(root)
        / "ExponentialEngine"
        / "flow-state"
        / "supervisor-generation-heartbeat.json"
    )
    if not file.exists():
        return False
    try:
        import datetime as dt

        value = json.loads(file.read_text(encoding="utf-8-sig"))
        at = dt.datetime.fromisoformat(value["heartbeat_at_utc"].replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - at).total_seconds()
        if value.get("status") == "RUNNING_CURRENT" and -10 <= age <= 120:
            return True
        # A heartbeat can go stale while a live worker saturates CPU/RAM.
        instance_file = file.parent / "supervisor-process-instance.json"
        if not instance_file.exists():
            return False
        instance = json.loads(instance_file.read_text(encoding="utf-8-sig"))
        pid = int(instance["worker"]["pid"])
        if pid <= 0:
            return True
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode:
            return True
        return any(
            row[1] == str(pid) and row[0].lower().startswith("python")
            for row in csv.reader(proc.stdout.splitlines())
            if len(row) >= 2
        )
    except (OSError, KeyError, ValueError, TypeError, subprocess.TimeoutExpired):
        return True  # Unreadable evidence fails closed.


def _git(path: Path, *args: str) -> str:
    run = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    return run.stdout.strip()


def prepare(destination: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite {destination}")
    destination.mkdir(parents=True)
    (destination / "src").mkdir()
    (destination / "tests").mkdir()
    (destination / "src" / "__init__.py").write_text("", encoding="utf-8")
    (destination / "src" / "tempo.py").write_text(BASELINE, encoding="utf-8")
    (destination / "tests" / "test_tempo.py").write_text(TESTS, encoding="utf-8")
    _git(destination, "init", "-q")
    _git(destination, "add", "--", "src", "tests")
    _git(
        destination,
        "-c",
        "user.name=Iron God Benchmark",
        "-c",
        "user.email=benchmark@localhost",
        "commit",
        "-qm",
        "baseline fixture",
    )


def safe_relative(name: str) -> Path:
    if not isinstance(name, str) or "\\" in name or "\0" in name:
        raise ValueError("Invalid target path")
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts or "." in parts.parts:
        raise ValueError("Traversal or absolute path rejected")
    if name not in ALLOWED:
        raise ValueError(f"Unapproved file: {name}")
    return Path(*parts.parts)


def apply_proposal(destination: Path, proposal: str) -> None:
    data = json.loads(proposal)
    if not isinstance(data, dict) or set(data) != {"files"}:
        raise ValueError("Proposal must contain exactly 'files'")
    files = data["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("Nonempty file map required")
    for name, content in files.items():
        rel = safe_relative(name)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Source must be nonempty text")
        target = destination.resolve() / rel
        if target.is_symlink() or not target.resolve().is_relative_to(
            destination.resolve()
        ):
            raise ValueError("Path escapes isolated fixture")
        if len(content.encode("utf-8")) > 100_000:
            raise ValueError("Source exceeds benchmark file budget")
    for name, content in files.items():
        (destination / safe_relative(name)).write_text(content, encoding="utf-8")


def verify(destination: Path) -> dict:
    changed = [
        x.replace("\\", "/")
        for x in _git(destination, "diff", "--name-only").splitlines()
    ]
    untracked = [
        x.replace("\\", "/")
        for x in _git(
            destination, "ls-files", "--others", "--exclude-standard"
        ).splitlines()
    ]
    unexpected = sorted(set(changed + untracked) - ALLOWED)
    changed_allowed = sorted(set(changed) & ALLOWED)
    outcome = {
        "changed_files": changed_allowed,
        "unexpected_files": unexpected,
        "tests_passed": False,
        "lint_passed": False,
        "accepted": False,
    }
    if unexpected or not changed_allowed:
        return outcome
    env = dict(os.environ)
    env["PYTHONPATH"] = str(destination)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    run = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=destination,
        env=env,
        text=True,
        capture_output=True,
        timeout=50,
    )
    outcome["tests_passed"] = run.returncode == 0
    outcome["test_report"] = (run.stdout + run.stderr)[-1600:]
    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src/tempo.py"],
        cwd=destination,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    outcome["lint_passed"] = lint.returncode == 0
    outcome["lint_report"] = (lint.stdout + lint.stderr)[-900:]
    outcome["accepted"] = outcome["tests_passed"] and outcome["lint_passed"]
    return outcome


def local_ollama_proposal(model: str) -> str:
    if live_supervisor():
        raise RuntimeError(
            "Iron God supervisor is active; decline competing local-model inference"
        )
    if memory_gib() < 3.0:
        raise RuntimeError("Below 3-GiB available-RAM admission floor")
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "keep_alive": "0s",
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1600},
        "messages": [
            {
                "role": "system",
                "content": "You are a precise Python developer. Reply with JSON only.",
            },
            {"role": "user", "content": TASK + "\nExisting file:\n" + BASELINE},
        ],
    }
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=420) as response:
        result = json.load(response)
    return result["message"]["content"]


def candidate_executable(name: str, command: str) -> str | None:
    """Resolve isolated installs without altering PATH or global user settings."""
    if command == "builtin":
        return "builtin"
    direct = shutil.which(command)
    if direct:
        return direct
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    root = Path(local) / "IronGodBuilderBench"
    paths = [
        root / "tools" / name / "node_modules" / ".bin" / (command + ".cmd"),
        root / "bin" / (command + ".exe"),
        root / "tools" / name / "goose-package" / (command + ".exe"),
    ]
    return next((str(p) for p in paths if p.is_file()), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["inventory", "prepare", "verify", "apply", "local-patch"]
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    args = parser.parse_args(argv)
    if args.command == "inventory":
        print(
            json.dumps(
                {
                    "candidates": [
                        {
                            "name": n,
                            "command": cmd,
                            "notes": notes,
                            "found": candidate_executable(n, cmd) is not None,
                            "executable": candidate_executable(n, cmd),
                        }
                        for n, cmd, notes in CANDIDATES
                    ],
                    "available_ram_gib": memory_gib(),
                    "live_supervisor": live_supervisor(),
                },
                indent=2,
            )
        )
        return 0
    if args.workspace is None:
        parser.error("--workspace is required")
    if args.command == "prepare":
        prepare(args.workspace)
        print("PREPARED " + str(args.workspace))
        return 0
    if args.command == "verify":
        result = verify(args.workspace)
        print(json.dumps(result, indent=2))
        return 0 if result["accepted"] else 2
    if args.command == "apply":
        if args.proposal is None:
            parser.error("--proposal is required for apply")
        apply_proposal(args.workspace, args.proposal.read_text(encoding="utf-8"))
    elif args.command == "local-patch":
        apply_proposal(args.workspace, local_ollama_proposal(args.model))
    result = verify(args.workspace)
    print(json.dumps(result, indent=2))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
