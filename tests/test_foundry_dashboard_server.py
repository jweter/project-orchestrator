import importlib.util,json
from pathlib import Path
from urllib.request import urlopen
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
