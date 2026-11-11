"""Give Me Control must also unload any resident Ollama model so the laptop is free for Rocksmith (owner 2026-11-10)."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HUMAN = ROOT / "scripts" / "desktop-control" / "Enter-HumanMode.ps1"
PS = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(PS is None, reason="Windows host required")


def _functions() -> str:
    text = HUMAN.read_text(encoding="utf-8")
    return "function Get-OllamaBase" + text.split("function Get-OllamaBase", 1)[1].split("function Find-OwnerWorkers", 1)[0]


def _server(loaded: list[str], seen: list[dict]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D401 - silence
            pass

        def do_GET(self):
            body = json.dumps({"models": [{"name": name} for name in loaded]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run(base: str) -> str:
    script = _functions() + "\n$out=@(); foreach($m in @(Get-LoadedOllamaModels)){ if(Unload-OllamaModel $m){$out+=('OK '+$m)} else {$out+=('FAIL '+$m)} }; $out -join ';'; (Get-OllamaBase)"
    done = subprocess.run([PS, "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60, check=False,
                          env={**__import__("os").environ, "OLLAMA_API_BASE": base})
    return done.stdout.strip()


def test_loaded_models_are_unloaded_with_keep_alive_zero():
    seen: list[dict] = []
    server = _server(["qwen2.5-coder:7b", "nomic-embed-text:latest"], seen)
    try:
        out = _run(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert "OK qwen2.5-coder:7b;OK nomic-embed-text:latest" in out
    assert [(call["model"], call["keep_alive"]) for call in seen] == [("qwen2.5-coder:7b", 0), ("nomic-embed-text:latest", 0)]


def test_a_non_loopback_endpoint_is_never_contacted():
    out = _run("http://10.255.255.1:11434")
    assert out.splitlines()[-1] == "http://127.0.0.1:11434"


def test_an_unreachable_ollama_is_a_quiet_no_op():
    out = _run("http://127.0.0.1:1")
    assert out.splitlines() == ["", "http://127.0.0.1:1"] or out.splitlines() == ["http://127.0.0.1:1"]


def test_the_handoff_calls_the_unload_after_killing_workers():
    source = HUMAN.read_text(encoding="utf-8")
    assert source.index("Stop-Process -Id $p.ProcessId") < source.index("Get-LoadedOllamaModels)") < source.index("Stop-ScheduledTask -TaskName 'Azathoth-Remote-Desktop-Commander'")
    assert "OLLAMA_UNLOADED" in source and "OLLAMA_UNLOAD_FAILED" in source
