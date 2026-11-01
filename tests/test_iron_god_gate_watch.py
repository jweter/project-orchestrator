"""The RAM-gate watcher must see the real mypy/preflight processes and never claim an unload it did not observe."""

from __future__ import annotations

import pytest

from project_orchestrator import iron_god_gate_watch as gw

PY = r"C:\Users\x\.venv\Scripts\python.exe"


@pytest.mark.parametrize("command,gate", [
    (f"{PY} -m mypy --config-file pyproject.toml pkg/extra.py tests/test_extra.py", "mypy"),
    (f'"{PY}" -m mypy src', "mypy"),
    (r"C:\x\.venv\Scripts\mypy.exe pkg", "mypy"),
    (f"{PY} engineering/preflight.py", "preflight"),
    (f"{PY} -u engineering\\preflight.py --full", "preflight"),
    (f'"{PY}" "C:\\work\\repo\\engineering\\preflight.py"', "preflight"),
    # mentions that are not gates
    ("git commit -m mypy", None),
    ("bash.exe -c python engineering/preflight.py", None),
    (f"{PY} -c print('mypy')", None),
    (f"{PY} scripts/iron_god_product_worker.py", None),
    (f"{PY} -m pytest tests/test_mypy.py", None),
    (f"{PY} -m project_orchestrator.iron_god_gate_watch --seconds 5 mypy", None),
    (f"{PY} -m mypy src/project_orchestrator/iron_god_gate_watch.py", "mypy"),  # checking the watcher's own file is a gate
    ("", None),
])
def test_only_processes_that_run_a_gate_are_gates(command: str, gate: str | None) -> None:
    assert gw.classify_gate(command) == gate


class Fake:
    """A scripted host: each tick the gate commands, resident models and free RAM come from the script."""

    def __init__(self, ticks: list[tuple[list[str], list[str] | None, float]]) -> None:
        self.ticks, self.now, self.index = ticks, 0.0, 0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.index += 1

    def current(self) -> tuple[list[str], list[str] | None, float]:
        return self.ticks[min(self.index, len(self.ticks) - 1)]

    def run(self) -> dict:
        return gw.watch(len(self.ticks), 1.0, model="qwen2.5-coder:7b", commands=lambda: self.current()[0],
                        models=lambda: self.current()[1], free_mb=lambda: self.current()[2], clock=self.clock,
                        sleep=self.sleep)


MYPY = [f"{PY} -m mypy pkg"]
PREFLIGHT = [f"{PY} engineering/preflight.py"]


def test_a_gate_with_no_resident_model_across_enough_samples_is_verified_unloaded() -> None:
    report = Fake([([], [], 6000), (MYPY, [], 4100), (MYPY, [], 3900), (MYPY, [], 4000), (PREFLIGHT, [], 3500),
                   (PREFLIGHT, [], 3400), (PREFLIGHT, [], 3600)]).run()

    assert report["gates"]["mypy"]["verdict"] == "VERIFIED_UNLOADED" and report["gates"]["mypy"]["min_free_mb"] == 3900
    assert report["gates"]["preflight"]["verdict"] == "VERIFIED_UNLOADED"
    assert report["min_free_mb"] == 3400 and report["samples"] == 7


def test_a_resident_model_during_a_gate_is_reported_even_once() -> None:
    report = Fake([(MYPY, [], 4000), (MYPY, ["qwen2.5-coder:7b"], 900), (MYPY, [], 4000)]).run()

    assert report["gates"]["mypy"]["verdict"] == "MODEL_RESIDENT"
    assert report["gates"]["mypy"]["model_resident_samples"] == 1 and report["gates"]["mypy"]["min_free_mb"] == 900


def test_a_different_resident_model_is_not_the_builder() -> None:
    report = Fake([(MYPY, ["nomic-embed-text:latest"], 4000)] * 3).run()

    assert report["gates"]["mypy"]["verdict"] == "VERIFIED_UNLOADED"


def test_too_few_samples_or_an_unreadable_ollama_is_never_called_verified() -> None:
    brief = Fake([([], [], 6000), (MYPY, [], 4000), ([], [], 6000)]).run()
    unreadable = Fake([(MYPY, None, 4000)] * 4).run()

    assert brief["gates"]["mypy"]["verdict"] == "INSUFFICIENT_SAMPLES"
    assert unreadable["gates"]["mypy"]["verdict"] == "INSUFFICIENT_SAMPLES"  # absence of an answer is not an unload


def test_a_gate_that_never_ran_makes_no_claim() -> None:
    report = Fake([([], [], 6000)] * 4).run()

    assert {row["verdict"] for row in report["gates"].values()} == {"NOT_OBSERVED"}


def test_the_watcher_only_reads() -> None:
    import inspect

    source = inspect.getsource(gw)
    for forbidden in ("keep_alive", "Stop-Process", "taskkill", "kill(", "terminate("):
        assert forbidden not in source
