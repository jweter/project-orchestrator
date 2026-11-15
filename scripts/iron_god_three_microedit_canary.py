"""Disposable three-real-edit acceptance/restart smoke proof for Issue #710.

This is a diagnostic, NOT an engine launch command or proof of autonomous
Ollama code generation. It only runs a hermetic pytest fixture that edits a
throwaway local Git repository and persists receipts in throwaway SQLite.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "tests" / "test_iron_god_three_microedit_resume_canary.py"


def run() -> tuple[int, dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="ig710-three-microedits-") as folder:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    str(TEST),
                    "--basetemp",
                    str(Path(folder) / "pytest"),
                    "-p",
                    "no:cacheprovider",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return 2, {
                "status": "DISPOSABLE_GIT_SQLITE_TEST_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "engine_started": False,
                "live_ollama_proven": False,
                "zero_cloud_token_use_proven": False,
                "product_reality_proven": False,
            }
        passed = result.returncode == 0 and "1 passed" in result.stdout
        return (0 if passed else 1), {
            "status": (
                "DISPOSABLE_THREE_MICROEDITS_AND_RESTART_PASSED"
                if passed
                else "DISPOSABLE_THREE_MICROEDIT_TEST_FAILED"
            ),
            "test_exit": result.returncode,
            "output_tail": (result.stdout + "\n" + result.stderr)[-1500:],
            "engine_started": False,
            "live_ollama_proven": False,
            "zero_cloud_token_use_proven": False,
            "product_reality_proven": False,
        }


if __name__ == "__main__":
    exit_code, receipt = run()
    print(json.dumps(receipt, sort_keys=True))
    raise SystemExit(exit_code)
