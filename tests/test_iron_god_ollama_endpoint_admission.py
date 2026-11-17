"""Regression tests for local Ollama service admission (issue #710).

The endpoint probe is read-only and cannot authorize a worker, change the
owner-controlled release, or weaken the existing RAM/lease gates.
"""

from pathlib import Path

from project_orchestrator import iron_god_local_model as local_model
from project_orchestrator import iron_god_product_lane as product_lane


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b'{"version":"0.40.16"}'):
        self.status = status
        self.body = body

    def read(self, _size: int) -> bytes:
        return self.body


def test_local_ollama_version_probe_succeeds_without_loading_a_model(monkeypatch):
    requests: list[tuple[str, str]] = []

    class FakeConnection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 11434, 0.2)

        def request(self, method, path):
            requests.append((method, path))

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
    monkeypatch.setattr(local_model.http.client, "HTTPConnection", FakeConnection)
    assert local_model.local_server_ready(timeout_s=0.2) is True
    assert requests == [("GET", "/api/version")]


def test_local_ollama_refused_connection_fails_closed(monkeypatch):
    closed: list[bool] = []

    class RefusedConnection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args):
            raise ConnectionRefusedError(10061, "connection refused")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(local_model.http.client, "HTTPConnection", RefusedConnection)
    assert local_model.local_server_ready() is False
    assert closed == [True]


def test_other_http_service_and_invalid_configuration_are_not_ollama(monkeypatch):
    class WrongService:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args):
            pass

        def getresponse(self):
            return FakeResponse(body=b'{"service":"not ollama"}')

        def close(self):
            pass

    monkeypatch.setattr(local_model.http.client, "HTTPConnection", WrongService)
    assert local_model.local_server_ready() is False
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:invalid")
    assert local_model.local_server_ready() is False


def test_dispatch_offline_does_not_sync_spawn_or_consume_worker_attempt(monkeypatch):
    monkeypatch.setattr(product_lane, "local_server_ready", lambda: False)
    monkeypatch.setattr(product_lane, "_report", lambda _lane, result: result)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no sync, worktree or worker is allowed before a healthy endpoint")

    result = product_lane.dispatch(
        object(), Path("unused"), {}, "jweter/knowledge-engine-ai",
        release_sha="abc", spawn=forbidden, sync=forbidden,
        owned_paths=forbidden, lease_id="existing-lease",
    )
    assert result["status"] == "WAITING_FOR_RESOURCES"
    assert result["admission"] == {"admitted": False, "reason": "OLLAMA_ENDPOINT_UNAVAILABLE"}
    assert result["next_action"] == "RESTORE_LOCAL_OLLAMA_SERVICE_WITH_RESOURCE_GATES_INTACT"
