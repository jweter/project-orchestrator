from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
_spec = importlib.util.spec_from_file_location("iron_god_model_bench", ROOT / "scripts" / "iron_god_model_bench.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


def test_a_finished_case_survives_a_crash_and_is_not_repeated(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    first = bench.Journal(path)
    first.add("case", "m:3b", workload="py", case="py-1", result={"workload": "py", "case": "py-1", "ok": True})
    # the process dies here; a brand new Journal (a rerun) reads everything back from disk
    again = bench.Journal(path)
    assert again.done("m:3b", "py", "py-1")["ok"] is True
    assert again.done("m:3b", "py", "py-2") is None and again.done("other:7b", "py", "py-1") is None


def test_a_torn_last_line_from_a_crash_is_ignored_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    journal = bench.Journal(path)
    journal.add("case", "m", workload="tools", case="tool-1", result={"ok": True})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "case", "model": "m", "workload": "tools", "case": "tool-2", "res')  # power cut mid-write
    again = bench.Journal(path)
    assert again.done("m", "tools", "tool-1") == {"ok": True} and again.done("m", "tools", "tool-2") is None
    again.add("case", "m", workload="tools", case="tool-2", result={"ok": False})  # and it keeps appending afterwards
    assert bench.Journal(path).done("m", "tools", "tool-2") == {"ok": False}


def test_atomic_write_never_leaves_a_half_written_summary(tmp_path: Path) -> None:
    target = tmp_path / "summary.json"
    bench.write_atomic(target, json.dumps({"a": 1}))
    bench.write_atomic(target, json.dumps({"a": 2}))
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2} and not list(tmp_path.glob("*.tmp"))


def test_generations_and_resources_are_journaled_as_they_happen(tmp_path: Path) -> None:
    journal = bench.Journal(tmp_path / "j.jsonl")

    class FakeClient:
        def generate(self, *_a, **_k):
            return {"response": "x", "eval_count": 10, "eval_duration": 1_000_000_000, "load_duration": 0}

    meter = bench.Meter(FakeClient(), "m", 1024, journal)
    meter.generate("hi")
    rows = bench.Journal(tmp_path / "j.jsonl").of("generation", "m")
    assert len(rows) == 1 and rows[0]["tok_s"] == 10.0
    resumed = bench.Meter(FakeClient(), "m", 1024, bench.Journal(tmp_path / "j.jsonl"))
    assert len(resumed.calls) == 1  # earlier generations are kept on a resumed run
