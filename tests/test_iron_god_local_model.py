"""The cancellable local-model client and its gates: the guard must interrupt an in-flight generation, not the caller."""

from __future__ import annotations

import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from project_orchestrator import iron_god_local_model as lm


class FakeOllama:
    """A server whose generation runs up to ``busy_s`` and records exactly when it notices the client went away."""

    def __init__(self, busy_s: float = 20.0) -> None:
        self.busy_s, self.loaded = busy_s, ["m:7b"]
        self.started_at = self.disconnected_at = self.finished_at = None
        self.requests: list[dict] = []
        self.error: str | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # noqa: D401 - silence the test server
                pass

            def _send(self, payload: dict) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._send({"models": [{"name": name} for name in outer.loaded]})

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if body.get("keep_alive") == 0:
                    outer.loaded = []
                    return self._send({"done": True})
                if outer.error:
                    return self._send({"error": outer.error})
                outer.started_at = time.monotonic()
                while time.monotonic() - outer.started_at < outer.busy_s:
                    readable, _, _ = select.select([self.connection], [], [], 0.05)
                    if readable and self.connection.recv(1, socket.MSG_PEEK) == b"":
                        outer.disconnected_at = time.monotonic()  # the generation would be cancelled here
                        return
                outer.finished_at = time.monotonic()
                self._send({"response": "done", "eval_count": 3, "eval_duration": 1_000_000_000})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def client(self) -> lm.OllamaClient:
        return lm.OllamaClient("127.0.0.1", self.port)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server():
    fake = FakeOllama()
    yield fake
    fake.close()


def test_a_guard_abort_closes_the_connection_so_the_server_stops_generating(server: FakeOllama) -> None:
    triggered_at: list[float] = []

    def abort() -> str | None:
        if time.monotonic() - server.started_at > 0.6 if server.started_at else False:
            triggered_at.append(time.monotonic())
            return "MEMORY_PRESSURE: test"
        return None

    began = time.monotonic()
    with pytest.raises(lm.ModelCancelled, match="MEMORY_PRESSURE"):
        server.client().generate("m:7b", "write a lot", num_ctx=512, num_predict=100, keep_alive="1m", timeout=60,
                                 should_abort=abort, poll_s=0.2)
    assert time.monotonic() - began < 5                       # the caller was released quickly...
    time.sleep(0.5)
    assert server.disconnected_at is not None                  # ...and the SERVER saw the disconnect (generation cancelled)
    assert server.finished_at is None                          # it did not run to completion
    assert server.disconnected_at - triggered_at[0] < 2.0      # within seconds of the guard firing


def test_without_an_abort_reason_the_generation_completes_normally() -> None:
    fake = FakeOllama(busy_s=0.4)
    try:
        reply = fake.client().generate("m:7b", "x", num_ctx=512, num_predict=10, keep_alive="1m", timeout=30,
                                       should_abort=lambda: None, poll_s=0.1)
        assert reply["response"] == "done" and fake.disconnected_at is None
    finally:
        fake.close()


def test_unload_drops_the_model_and_is_confirmed_by_the_server(server: FakeOllama) -> None:
    client = server.client()
    assert client.loaded() == ["m:7b"]
    assert client.unload("m:7b", wait_s=3, poll_s=0.05) is True
    assert client.loaded() == [] and server.requests[-1]["keep_alive"] == 0


def test_unload_reports_failure_when_the_server_keeps_the_model_loaded(server: FakeOllama) -> None:
    class Stubborn(lm.OllamaClient):
        def loaded(self) -> list[str]:
            return ["m:7b"]

    assert Stubborn("127.0.0.1", server.port).unload("m:7b", wait_s=0.3, poll_s=0.05) is False


def test_a_server_that_is_down_is_an_ordinary_model_failure() -> None:
    client = lm.OllamaClient("127.0.0.1", 1)  # nothing listens here
    with pytest.raises(OSError):
        client.generate("m:7b", "x", num_ctx=512, num_predict=1, keep_alive="1m", timeout=2)


def gate(free: list[float], idle: float = 3600.0, **over) -> lm.Gate:
    readings = iter(free)
    return lm.Gate(model_gib=over.pop("model_gib", 5.3), available=lambda: next(readings, free[-1]), idle=lambda: idle, **over)


def test_admission_needs_available_memory_for_the_model_plus_a_reserve() -> None:
    assert gate([8.0]).admit() == (True, "ADMITTED")
    refused, reason = gate([6.0]).admit()
    assert refused is False and reason.startswith("MEMORY: 6.0 GiB available < 7.3 GiB required")
    assert gate([3.5], model_gib=1.9, reserve_gib=1.5).admit()[0] is True


def test_an_idle_only_model_is_not_admitted_while_the_user_is_active() -> None:
    refused, reason = gate([9.0], idle=30.0, min_idle_s=600).admit()
    assert refused is False and reason.startswith("USER_ACTIVE")
    assert gate([9.0], idle=900.0, min_idle_s=600).admit()[0] is True
    assert gate([9.0], idle=1.0, min_idle_s=0).admit()[0] is True      # a small model beside the desktop needs no idleness


def test_abort_needs_sustained_pressure_and_remembers_the_lowest_reading() -> None:
    g = gate([8.0, 0.3, 4.0, 0.3, 8.0], idle=900.0)
    assert [g.abort_reason() for _ in range(5)] == [None] * 5            # single dips and recoveries never abort
    g2 = gate([8.0, 0.3, 0.2], idle=900.0)
    first, second = g2.abort_reason(), g2.abort_reason()
    assert first is None and second is not None and second.startswith("MEMORY_PRESSURE") and g2.lowest <= 0.3


def test_returning_user_input_aborts_an_idle_only_run_but_not_a_small_model_run() -> None:
    assert gate([9.0], idle=2.0, min_idle_s=600).abort_reason() == (
        "USER_RETURNED: keyboard or mouse input while an idle-only model was running")
    assert gate([9.0], idle=2.0, min_idle_s=0).abort_reason() is None


def test_the_system_probes_return_plausible_values() -> None:
    assert 0.1 < lm.available_gib() < 4096
    assert lm.user_idle_seconds() >= 0.0


# ---- the shared guarded door -------------------------------------------------------------------------------------------


class FakeDoor:
    def __init__(self, abort_after_poll=False):
        self.calls, self.unloaded, self.abort = [], [], abort_after_poll

    def generate(self, model, prompt, **kw):
        self.calls.append((model, kw))
        if self.abort:
            for _ in range(3):  # the real client polls every second
                reason = kw["should_abort"]()
                if reason:
                    raise lm.ModelCancelled(reason)
        return {"response": "{}"}

    stray: list = []

    def loaded(self):
        return list(self.stray)

    def unload(self, model, **kw):
        self.unloaded.append(model)
        return True


def door_probes(free, idle=0.0):
    return {"available": lambda: free, "idle": lambda: idle}


def test_default_is_3b_and_a_larger_request_downgrades_unless_it_is_admitted_right_now():
    assert lm.choose_model(None, **door_probes(6.0))[0] == "qwen2.5-coder:3b"
    assert lm.choose_model("ollama/qwen2.5-coder:7b", **door_probes(6.0, idle=900))[0] == "qwen2.5-coder:3b"   # memory
    assert lm.choose_model("ollama/qwen2.5-coder:7b", **door_probes(9.0, idle=10))[0] == "qwen2.5-coder:3b"    # user active
    assert lm.choose_model("ollama/qwen2.5-coder:7b", **door_probes(9.0, idle=900))[0] == "qwen2.5-coder:7b"
    assert lm.gate_for("some-unknown-model").model_gib == 5.3                                              # strictest


def test_guarded_generate_loads_nothing_when_memory_is_short_and_always_unloads_after(tmp_path):
    fake = FakeDoor()
    with pytest.raises(lm.ModelNotAdmitted, match="MEMORY"):
        lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=0.0, client=fake, probes=door_probes(1.0), directory=tmp_path / "slot")
    assert fake.calls == [] and not (tmp_path / "slot").exists()
    reply = lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=5, client=fake, probes=door_probes(6.0), directory=tmp_path / "slot")
    assert reply["model_used"] == "qwen2.5-coder:3b" and fake.unloaded == ["qwen2.5-coder:3b"]
    assert fake.calls[0][1]["keep_alive"] == 0 and not (tmp_path / "slot").exists()


def test_only_one_model_run_holds_the_slot_and_a_dead_owner_is_recovered(tmp_path):
    slot = tmp_path / "slot"
    with lm.model_slot(1, directory=slot):
        with pytest.raises(lm.ModelNotAdmitted, match="SLOT_BUSY"):
            with lm.model_slot(0.0, directory=slot, poll_s=0.01):
                pass
    slot.mkdir()
    (slot / "owner").write_text("999999\n1\n", encoding="utf-8")
    with lm.model_slot(1, directory=slot, alive=lambda pid: False):
        assert (slot / "owner").exists()


def test_pressure_during_a_run_cancels_it_and_the_model_is_still_unloaded(tmp_path):
    fake, free = FakeDoor(abort_after_poll=True), iter([6.0, 6.0, 6.0, 0.1, 0.1, 0.1])
    gate_probes = {"available": lambda: next(free), "idle": lambda: 0.0}
    with pytest.raises(lm.ModelCancelled):
        lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=5, client=fake, probes=gate_probes, directory=tmp_path / "slot")
    assert fake.unloaded == ["qwen2.5-coder:3b"]


def test_the_size_token_is_matched_exactly_so_13b_never_borrows_the_3b_profile():
    for name in ("ollama/codellama:13b", "x:30b", "qwen:0.3b", "mystery-model"):
        assert lm.gate_for(name).model_gib == 5.3 and lm.gate_for(name).min_idle_s == 600.0
    assert lm.gate_for("ollama/qwen2.5-coder:3b-instruct-q4").model_gib == 2.3
    assert lm.gate_for("qwen2.5-coder:1.5b").model_gib == 1.1
    assert lm.choose_model("ollama/codellama:13b", **door_probes(4.0))[0] == "qwen2.5-coder:3b"


def test_the_shared_client_follows_the_configured_endpoint(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://10.1.2.3:9999/")
    client = lm.OllamaClient()
    assert (client.host, client.port) == ("10.1.2.3", 9999)
    monkeypatch.delenv("OLLAMA_API_BASE")
    assert (lm.OllamaClient().host, lm.OllamaClient().port) == ("127.0.0.1", 11434)


def test_qwen35_sizes_have_their_own_profiles_and_unknown_sizes_stay_strict():
    assert lm.gate_for("qwen3.5:4b").model_gib == 4.5 and lm.gate_for("qwen3.5:2b").model_gib == 2.8
    assert lm.gate_for("qwen3.5:0.8b").model_gib == 1.3 and lm.gate_for("qwen3.5:9b").min_idle_s == 600.0
    assert lm.gate_for("qwen3.5:27b").model_gib == 5.3 and lm.gate_for("qwen3.5:27b").min_idle_s == 600.0


def test_a_stray_resident_model_is_evicted_before_the_run_and_thinking_is_off_for_qwen3(tmp_path):
    fake = FakeDoor()
    fake.stray = ["some-other:7b"]
    lm.guarded_generate("p", requested="qwen3.5:4b", num_ctx=1, num_predict=1, wait_s=5, client=fake,
                        probes=door_probes(9.0), directory=tmp_path / "slot")
    assert fake.unloaded[0] == "some-other:7b" and fake.calls[0][0] == "qwen3.5:4b"
    sent = {}

    class Capture(lm.OllamaClient):
        def __init__(self):
            super().__init__("127.0.0.1", 1)

    import http.client as hc
    original = hc.HTTPConnection.request
    hc.HTTPConnection.request = lambda self, m, path, body=None, headers=None: sent.update(body=body)
    try:
        with pytest.raises(Exception):
            Capture().generate("qwen3.5:4b", "p", num_ctx=1, num_predict=1, keep_alive=0, timeout=0.2)
    finally:
        hc.HTTPConnection.request = original
    assert json.loads(sent["body"])["think"] is False


def test_a_stray_model_that_cannot_be_unloaded_blocks_the_run_and_loads_nothing(tmp_path):
    fake = FakeDoor()
    fake.stray = ["stubborn:7b"]
    fake.unload = lambda model, **kw: False
    with pytest.raises(lm.ModelNotAdmitted, match="STRAY_MODEL_RESIDENT"):
        lm.guarded_generate("p", num_ctx=1, num_predict=1, wait_s=5, client=fake, probes=door_probes(9.0),
                            directory=tmp_path / "slot")
    assert fake.calls == [] and not (tmp_path / "slot").exists()


def test_an_error_body_from_the_server_is_a_failure_not_an_empty_answer() -> None:
    fake = FakeOllama(busy_s=0.1)
    fake.error = "error starting llama-server: llama-server binary not found"
    try:
        with pytest.raises(OSError, match="OLLAMA_ERROR.*llama-server"):
            fake.client().generate("m:7b", "x", num_ctx=512, num_predict=10, keep_alive="0", timeout=30, should_abort=lambda: None, poll_s=0.1)
    finally:
        fake.close()
