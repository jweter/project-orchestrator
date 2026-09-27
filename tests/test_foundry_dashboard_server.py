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
    finally: httpd.shutdown()


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
    for action in ("STATUS_SNAPSHOT","RESTART_REMOTE_CONTROL","RECOVER_CONTROL_PLANE","REBOOT_WINDOWS"):
        assert action in html
    assert "Authorization" in js and "Bearer " in js
    assert "ARBITRARY_SHELL" not in html
