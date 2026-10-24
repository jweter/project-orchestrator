"""Combined regression verification.

Runs a full-suite regression check (not a single slice's focused tests)
under the exact-head guard, and wraps the result as a single-gate
Verification Receipt. Formalizes, as a reusable module, the same manual
full-pytest + full-preflight check already performed by hand throughout
this project's history: this is the additional Tier-3 "did combining
multiple candidates break anything else" check, never a substitute for
a slice's own focused-test requirement.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from .iron_god_exact_head_guard import run_with_exact_head_guard
from .iron_god_verification_receipt import (
    GateResult,
    VerificationReceipt,
    build_receipt,
)

RegressionRunner = Callable[[Path], tuple[int, bool, bool]]


class CombinedRegressionError(RuntimeError):
    """Raised when combined regression verification cannot produce a
    trustworthy receipt."""


def default_pytest_runner(timeout: int = 600) -> RegressionRunner:
    """Return a regression runner that shells out to `python -m pytest -q`
    in `repo_root`, mapping its outcome to (returncode, timed_out,
    interrupted)."""

    def runner(repo_root: Path) -> tuple[int, bool, bool]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return (result.returncode, False, False)
        except subprocess.TimeoutExpired:
            return (1, True, False)

    return runner


def run_combined_regression(
    repo_root: Path,
    repository: str,
    expected_sha: str,
    *,
    regression_runner: RegressionRunner,
) -> VerificationReceipt:
    """Run `regression_runner(repo_root)` under the exact-head guard and
    wrap its outcome as a one-gate "combined_regression" receipt.

    The guard ensures the receipt is never attributed to the wrong
    commit: if HEAD does not already match `expected_sha`, the runner is
    never called; if HEAD moves during the run, this raises
    CombinedRegressionError instead of returning any receipt.
    """

    def step() -> GateResult:
        returncode, timed_out, interrupted = regression_runner(repo_root)
        return GateResult(
            name="combined_regression",
            returncode=returncode,
            timed_out=timed_out,
            interrupted=interrupted,
        )

    try:
        gate = run_with_exact_head_guard(repo_root, expected_sha, step)
    except Exception as exc:
        raise CombinedRegressionError(
            f"combined regression verification did not produce a trustworthy "
            f"result at {expected_sha!r}: {exc}"
        ) from exc

    return build_receipt(expected_sha, repository, (gate,))
