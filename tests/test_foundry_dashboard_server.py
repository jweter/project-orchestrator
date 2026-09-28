import importlib.util,json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from threading import Thread
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("dashboard_server",ROOT/"scripts"/"foundry_dashboard_server.py")
server=importlib.util.module_from_spec(spec); spec.loader.exec_module(server)

def test_dashboard_server_serves_snapshot_and_pwa(tmp_path):
    telemetry=tmp_path/"telemetry"; telemetry.mkdir()
    (telemetry/"foundry-dashboard.json").write_text(json.dumps({"active_count":2}),encoding="utf-8")
    httpd=ThreadingHTTPServer(("127.0.0.1",0),server.handler_for(tmp_path,ROOT/"dashboard"))
    Thread(target=httpd.serve_forever,daemon=True).start()
    try:
        base=f"http://127.0.0.1:{httpd.server_port}"
        assert json.load(urlopen(base+"/api/dashboard"))["active_count"]==2
        html=urlopen(base+"/").read().decode()
        assert "Azathoth Foundry" in html and "manifest.webmanifest" in html
        manifest=json.load(urlopen(base+"/manifest.webmanifest"))
        assert manifest["display"]=="standalone"
        health=json.load(urlopen(base+"/api/health"))
        assert set(health["subsystems"])=={"windows","dashboard_snapshot","foundry_supervisor","github_guardian","remote_control"}
        assert all(row["status"] in {"LIVE","STALE","OFFLINE","UNKNOWN"} for row in health["subsystems"].values())
        assert set(("machine","alerts","human_mode","command_progress","control_history","overall_status")) <= set(health)
    finally: httpd.shutdown()


def test_overall_control_status_ignores_stale_dashboard_snapshot():
    subsystems={
        "windows":{"status":"LIVE"},
        "dashboard_snapshot":{"status":"STALE"},
        "foundry_supervisor":{"status":"LIVE"},
        "github_guardian":{"status":"LIVE"},
        "remote_control":{"status":"LIVE"},
    }
    assert server._overall_control_status(subsystems)=="LIVE"
    subsystems["remote_control"]["status"]="OFFLINE"
    assert server._overall_control_status(subsystems)=="DEGRADED"
    subsystems["remote_control"]["status"]="UNKNOWN"
    assert server._overall_control_status(subsystems)=="CHECKING"


def test_control_endpoint_requires_token_and_rejects_unallowlisted_action(tmp_path):
    control=tmp_path/"control-repo"; (control/"control").mkdir(parents=True)
    httpd=ThreadingHTTPServer(("127.0.0.1",0),server.handler_for(tmp_path,ROOT/"dashboard",control,"secret"))
    Thread(target=httpd.serve_forever,daemon=True).start()
    try:
        base=f"http://127.0.0.1:{httpd.server_port}"
        req=Request(base+"/api/control",data=b'{"action":"REBOOT_WINDOWS"}',headers={"Content-Type":"application/json"},method="POST")
        try: urlopen(req)
        except HTTPError as exc: assert exc.code==401
        else: raise AssertionError("unauthenticated control request accepted")
        req=Request(base+"/api/control",data=b'{"action":"ARBITRARY_SHELL"}',headers={"Content-Type":"application/json","Authorization":"Bearer secret"},method="POST")
        try: urlopen(req)
        except HTTPError as exc: assert exc.code==400
        else: raise AssertionError("unallowlisted control action accepted")
    finally: httpd.shutdown()


def test_dashboard_exposes_only_bounded_control_actions():
    js=(ROOT/"dashboard"/"app.js").read_text(encoding="utf-8")
    html=(ROOT/"dashboard"/"index.html").read_text(encoding="utf-8")
    for action in ("STATUS_SNAPSHOT","RESTART_REMOTE_CONTROL","RECOVER_CONTROL_PLANE","REBOOT_WINDOWS","HUMAN_MODE","FULL_POWER"):
        assert action in html
    assert "Authorization" in js and "Bearer " in js
    assert "ARBITRARY_SHELL" not in html
    assert "Wake Azathoth" in html and "Wake relay settings" in html
    assert "azathothWakeRelayUrl" in js and "azathothWakeRelayToken" in js
    sw=(ROOT/"dashboard"/"sw.js").read_text(encoding="utf-8")
    assert 'url.pathname.startsWith("/api/")' in sw
    assert "url.origin!==self.location.origin" in sw


def test_human_mode_control_bypasses_git_mailbox_and_works_without_control_repo(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(server,"_invoke_local_mode",lambda action,state_root: calls.append((action,state_root)) or {"status":"COMPLETED"})
    httpd=ThreadingHTTPServer(("127.0.0.1",0),server.handler_for(tmp_path,ROOT/"dashboard",None,"secret"))
    Thread(target=httpd.serve_forever,daemon=True).start()
    try:
        base=f"http://127.0.0.1:{httpd.server_port}"
        req=Request(base+"/api/control",data=b'{"action":"HUMAN_MODE"}',
                    headers={"Content-Type":"application/json","Authorization":"Bearer secret"},method="POST")
        payload=json.load(urlopen(req))
        assert payload["status"]=="COMPLETED"
        assert calls==[("HUMAN_MODE",tmp_path)]
    finally:
        httpd.shutdown()


def test_full_power_control_uses_same_local_control_path(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(server,"_invoke_local_mode",lambda action,state_root: calls.append((action,state_root)) or {"status":"COMPLETED"})
    httpd=ThreadingHTTPServer(("127.0.0.1",0),server.handler_for(tmp_path,ROOT/"dashboard",None,"secret"))
    Thread(target=httpd.serve_forever,daemon=True).start()
    try:
        base=f"http://127.0.0.1:{httpd.server_port}"
        req=Request(base+"/api/control",data=b'{"action":"FULL_POWER"}',
                    headers={"Content-Type":"application/json","Authorization":"Bearer secret"},method="POST")
        payload=json.load(urlopen(req))
        assert payload["status"]=="COMPLETED"
        assert calls==[("FULL_POWER",tmp_path)]
    finally:
        httpd.shutdown()
