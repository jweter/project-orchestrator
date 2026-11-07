"""Cancellable local-model client and the safety gates that decide when a local model may run on a shared desktop.

Why this exists: stopping the *calling* thread does not stop inference. Ollama keeps generating until the client
connection goes away, and a ``keep_alive: 0`` request cannot unload a model that has an active request. So a memory or
activity guard has to (1) close the in-flight connection, which makes the server cancel the generation, (2) prove the
runner went idle, and only then (3) unload the model to hand its memory back.

* ``OllamaClient.generate`` runs the request on a worker thread and polls ``should_abort``; on a reason it shuts the socket
  down, so the server sees the disconnect, and raises ``ModelCancelled``.
* ``Gate`` combines the three conditions a model run needs on Azathoth: enough *available* memory plus a reserve, a user
  who has been idle long enough, and (continuously, during the run) sustained memory pressure or returning user input as
  abort reasons.
* All system probes are injectable, so the logic is tested without a model, a GPU or a desktop.
"""

from __future__ import annotations

import ctypes
import http.client
import json
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


class ModelCancelled(OSError):
    """The run was aborted by a guard; the message names the reason."""


def available_gib() -> float:
    """Physical memory available to a new process (free plus reclaimable cache), in GiB."""

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = Status()
    status.dwLength = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
    return float(status.ullAvailPhys) / 1024**3


def user_idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input of the interactive user."""

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(LastInput)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))  # type: ignore[attr-defined]
    return float((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0  # type: ignore[attr-defined]


@dataclass
class Gate:
    """When may a model of ``model_gib`` run, and when must a running one stop?

    Admission needs available memory of at least ``model_gib + reserve_gib`` and ``min_idle_s`` of user inactivity (set it
    to 0 for a small model that fits beside the desktop). During a run, ``abort_reason`` reports sustained pressure
    (``patience`` readings under ``abort_below_gib``) or, when idleness is required, returning user input.
    """

    model_gib: float
    reserve_gib: float = 2.0
    min_idle_s: float = 0.0
    abort_below_gib: float = 0.6
    patience: int = 2
    available: Callable[[], float] = available_gib
    idle: Callable[[], float] = user_idle_seconds

    def __post_init__(self) -> None:
        self._low = 0
        self.lowest = self.available()

    @property
    def required_gib(self) -> float:
        return self.model_gib + self.reserve_gib

    def admit(self) -> tuple[bool, str]:
        free, idle = self.available(), self.idle()
        self.lowest = min(self.lowest, free)
        if free < self.required_gib:
            return False, f"MEMORY: {free:.1f} GiB available < {self.required_gib:.1f} GiB required"
        if idle < self.min_idle_s:
            return False, f"USER_ACTIVE: idle {idle:.0f}s < {self.min_idle_s:.0f}s required"
        return True, "ADMITTED"

    def abort_reason(self) -> str | None:
        free = self.available()
        self.lowest = min(self.lowest, free)
        self._low = self._low + 1 if free < self.abort_below_gib else 0
        if self._low >= self.patience:
            return f"MEMORY_PRESSURE: available stayed under {self.abort_below_gib} GiB (lowest {self.lowest:.2f} GiB)"
        if self.min_idle_s and self.idle() < min(self.min_idle_s, 5.0):
            return "USER_RETURNED: keyboard or mouse input while an idle-only model was running"
        return None


class OllamaClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 11434) -> None:
        self.host, self.port = host, port

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
            return dict(json.loads(conn.getresponse().read().decode("utf-8") or "{}"))
        finally:
            conn.close()

    def generate(self, model: str, prompt: str, *, num_ctx: int, num_predict: int, keep_alive: str, timeout: float,
                 should_abort: Callable[[], str | None] | None = None, poll_s: float = 1.0) -> dict[str, Any]:
        """One non-streaming generation that a guard can cancel: the connection is shut down, the server cancels."""
        body = json.dumps({"model": model, "prompt": prompt, "stream": False, "keep_alive": keep_alive,
                           "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict}})
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        outcome: dict[str, Any] = {}

        def call() -> None:
            try:
                conn.request("POST", "/api/generate", body, {"Content-Type": "application/json"})
                outcome["reply"] = json.loads(conn.getresponse().read().decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - handed back to the caller as a model failure
                outcome["error"] = exc

        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(poll_s)
            reason = should_abort() if (should_abort and worker.is_alive()) else None
            if reason:
                self.cancel(conn)
                worker.join(10.0)
                raise ModelCancelled(reason)
        if "error" in outcome:
            raise outcome["error"]
        return dict(outcome["reply"])

    @staticmethod
    def cancel(conn: http.client.HTTPConnection) -> None:
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        conn.close()

    def loaded(self) -> list[str]:
        return [str(m.get("name")) for m in self._request("GET", "/api/ps").get("models") or []]

    def unload(self, model: str, *, wait_s: float = 30.0, poll_s: float = 0.5) -> bool:
        """Ask the server to drop the model and confirm it is gone; True once it no longer appears as loaded."""
        try:
            self._request("POST", "/api/generate", {"model": model, "keep_alive": 0}, timeout=30.0)
        except OSError:
            pass
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                if model not in self.loaded():
                    return True
            except OSError:
                return False
            time.sleep(poll_s)
        return False


INFERENCE_PROCESSES = ("llama-server", "llama_server", "ollama_llama_server", "ollama*")


def inference_cpu_seconds() -> float:
    """Total processor time used so far by the processes that run inference.

    Measured on Azathoth (2026-11-06): Ollama's generation runs in a separate ``llama-server`` process (about ten cores busy,
    ~1 GB for a 1.5B model); the ``ollama`` processes themselves stay flat. Watching only ``ollama*`` proves nothing.
    """
    names = ",".join(f"'{name}'" for name in INFERENCE_PROCESSES)
    script = f"(Get-Process -Name {names} -ErrorAction SilentlyContinue | Measure-Object -Property CPU -Sum).Sum"
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, check=False, timeout=30)
    try:
        return float(done.stdout.strip() or 0.0)
    except ValueError:
        return 0.0
