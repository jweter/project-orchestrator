import importlib.util,json
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("wake_relay",ROOT/"scripts"/"wake_relay_server.py")
relay=importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)

def test_magic_packet_is_standard_wol_shape():
    packet=relay.magic_packet("F8-FE-5E-AE-DE-B3")
    assert len(packet)==102
    assert packet[:6]==b"\xff"*6
    assert packet[6:12]==bytes.fromhex("F8FE5EAEDEB3")

def test_relay_requires_auth_and_fixed_target(monkeypatch):
    sent=[]
    monkeypatch.setattr(relay,"send_magic_packet",lambda mac,broadcast,port: sent.append((mac,broadcast,port)))
    httpd=ThreadingHTTPServer(("127.0.0.1",0),relay.handler_for(
        token="secret",mac="F8-FE-5E-AE-DE-B3",broadcast="192.168.1.255",allowed_origins=("https://azathoth.example",)))
    Thread(target=httpd.serve_forever,daemon=True).start()
    try:
        base=f"http://127.0.0.1:{httpd.server_port}"
        try: urlopen(base+"/health")
        except HTTPError as exc: assert exc.code==401
        else: raise AssertionError("unauthenticated relay health accepted")

        bad=Request(base+"/wake",data=b'{"mac":"00:11:22:33:44:55"}',
                    headers={"Authorization":"Bearer secret","Content-Type":"application/json"},method="POST")
        try: urlopen(bad)
        except HTTPError as exc:
            assert exc.code==400
            assert json.load(exc)["status"]=="FIXED_TARGET_ONLY"
        else: raise AssertionError("relay accepted caller-supplied target")
        assert sent==[]

        good=Request(base+"/wake",data=b"{}",
                     headers={"Authorization":"Bearer secret","Content-Type":"application/json"},method="POST")
        assert json.load(urlopen(good))["status"]=="WAKE_SENT"
        assert sent==[("F8FE5EAEDEB3","192.168.1.255",9)]
    finally:
        httpd.shutdown()

def test_relay_cors_is_exact_origin_only():
    httpd=ThreadingHTTPServer(("127.0.0.1",0),relay.handler_for(
        token="secret",mac="F8-FE-5E-AE-DE-B3",broadcast="192.168.1.255",allowed_origins=("https://azathoth.example",)))
    Thread(target=httpd.serve_forever,daemon=True).start()
    try:
        base=f"http://127.0.0.1:{httpd.server_port}"
        req=Request(base+"/health",headers={"Authorization":"Bearer secret","Origin":"https://azathoth.example"})
        res=urlopen(req)
        assert res.headers["Access-Control-Allow-Origin"]=="https://azathoth.example"
        req=Request(base+"/health",headers={"Authorization":"Bearer secret","Origin":"https://evil.example"})
        res=urlopen(req)
        assert res.headers.get("Access-Control-Allow-Origin") is None
    finally:
        httpd.shutdown()
