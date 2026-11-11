from __future__ import annotations

import json
from types import SimpleNamespace

from project_orchestrator import iron_god_flight_recorder as fr


class FakeProc:
    def __init__(self, pid, name, cpu, rss_mb, boom=False):
        self.info = {"pid": pid, "name": name}
        self._cpu, self._rss, self._boom = cpu, rss_mb, boom

    def cpu_percent(self, interval=None):
        if self._boom:
            raise PermissionError("denied")
        return self._cpu

    def memory_info(self):
        return SimpleNamespace(rss=self._rss * 1024 * 1024)


class FakePsutil:
    def __init__(self, procs):
        self.procs = procs

    def cpu_percent(self, interval=None):
        return 61.25

    def virtual_memory(self):
        return SimpleNamespace(available=3 * 1024**3, percent=80.04)

    def swap_memory(self):
        return SimpleNamespace(percent=12.0)

    def process_iter(self, attrs=None):
        return iter(self.procs)


PROCS = [FakeProc(1, "llama-server.exe", 640.0, 5200), FakeProc(2, "chrome.exe", 5.0, 900), FakeProc(3, "NortonSecurity.exe", 30.0, 300),
         FakeProc(4, "ollama.exe", 1.0, 80), FakeProc(5, "locked.exe", 0, 0, boom=True), FakeProc(6, "notepad.exe", 0.0, 40)]


def make(tmp_path):
    return fr.Recorder(FakePsutil(PROCS), disk_root=str(tmp_path))


def test_a_sample_shows_the_heaviest_processes_and_the_watched_ones(tmp_path):
    entry = make(tmp_path).sample(now=0)
    assert entry["ts"].startswith("1970-01-01T00:00:00") and entry["cpu_pct"] == 61.2 and entry["mem_avail_mb"] == 3072
    assert entry["top_cpu"][0]["name"] == "llama-server.exe" and entry["top_rss"][0]["rss_mb"] == 5200
    watched = {row["name"] for row in entry["watched"]}
    assert {"llama-server.exe", "NortonSecurity.exe", "ollama.exe"} <= watched and "chrome.exe" not in watched
    assert "locked.exe" not in {row["name"] for row in entry["top_cpu"]}            # a denied process never stops the sample
    assert entry["swap_used_pct"] == 12.0 and entry["disk_free_gb"] >= 0


def test_each_line_is_durable_json_and_files_rotate_without_growing_forever(tmp_path):
    path = tmp_path / "rec" / "recorder.jsonl"
    recorder = make(tmp_path)
    for tick in range(40):
        fr.append(path, recorder.sample(now=tick))
        fr.rotate(path, max_bytes=2000, keep=3)
    assert all(json.loads(line)["cpu_pct"] == 61.2 for line in path.read_text().splitlines())
    names = sorted(p.name for p in path.parent.iterdir())
    assert names == ["recorder.jsonl", "recorder.jsonl.1", "recorder.jsonl.2"]


def test_the_loop_survives_a_failing_write_and_stops_when_asked(tmp_path, monkeypatch):
    calls = {"n": 0}
    original = fr.append

    def flaky(path, entry):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        original(path, entry)

    monkeypatch.setattr(fr, "append", flaky)
    stops = iter([False, False, False, True])
    written = fr.run(tmp_path / "r.jsonl", 5.0, recorder=make(tmp_path), should_stop=lambda: next(stops), sleep=lambda s: None,
                     clock=lambda: 0.0)
    assert written == 2 and calls["n"] == 3
    assert len((tmp_path / "r.jsonl").read_text().splitlines()) == 2
