#!/usr/bin/env python3
"""Minimal authenticated Wake-on-LAN relay for an always-on LAN host."""
import argparse,json,os,secrets,socket,ssl,time
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

def normalize_mac(value:str)->str:
    raw="".join(ch for ch in value if ch.isalnum()).upper()
    if len(raw)!=12 or any(ch not in "0123456789ABCDEF" for ch in raw):
        raise ValueError("invalid MAC address")
    return raw

def magic_packet(mac:str)->bytes:
    raw=bytes.fromhex(normalize_mac(mac))
    return b"\xff"*6+raw*16

def send_magic_packet(mac:str,broadcast:str,port:int=9,repeats:int=3)->None:
    packet=magic_packet(mac)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
        for _ in range(repeats):
            sock.sendto(packet,(broadcast,port))
            time.sleep(0.08)

def handler_for(*,token:str,mac:str,broadcast:str,port:int=9,allowed_origins:tuple[str,...]=()):
    fixed_mac=normalize_mac(mac)
    state={"last_wake_at":None,"wake_count":0}
    class Handler(BaseHTTPRequestHandler):
        def _origin(self)->str|None:
            origin=self.headers.get("Origin")
            return origin if origin in allowed_origins else None
        def _headers(self,status:int):
            self.send_response(status)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            origin=self._origin()
            if origin:
                self.send_header("Access-Control-Allow-Origin",origin)
                self.send_header("Vary","Origin")
            self.end_headers()
        def _json(self,status:int,payload:dict):
            data=json.dumps(payload,sort_keys=True).encode()
            self._headers(status); self.wfile.write(data)
        def _authorized(self)->bool:
            supplied=self.headers.get("Authorization","").removeprefix("Bearer ").strip()
            return bool(token and supplied and secrets.compare_digest(supplied,token))
        def do_OPTIONS(self):
            origin=self._origin()
            if not origin:
                self.send_response(403); self.end_headers(); return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin",origin)
            self.send_header("Access-Control-Allow-Headers","Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age","600")
            self.end_headers()
        def do_GET(self):
            if self.path.split("?",1)[0]!="/health":
                self.send_response(404); self.end_headers(); return
            if not self._authorized():
                self._json(401,{"status":"UNAUTHORIZED"}); return
            self._json(200,{"status":"READY","last_wake_at":state["last_wake_at"],"wake_count":state["wake_count"]})
        def do_POST(self):
            if self.path.split("?",1)[0]!="/wake":
                self.send_response(404); self.end_headers(); return
            if not self._authorized():
                self._json(401,{"status":"UNAUTHORIZED"}); return
            try:
                length=min(int(self.headers.get("Content-Length","0")),4096)
                body=json.loads(self.rfile.read(length) or b"{}")
            except (ValueError,json.JSONDecodeError):
                self._json(400,{"status":"INVALID_JSON"}); return
            if body not in ({},None):
                self._json(400,{"status":"FIXED_TARGET_ONLY"}); return
            try:
                send_magic_packet(fixed_mac,broadcast,port)
            except OSError:
                self._json(503,{"status":"WAKE_SEND_FAILED"}); return
            state["last_wake_at"]=datetime.now(timezone.utc).isoformat()
            state["wake_count"]+=1
            self._json(202,{"status":"WAKE_SENT","at":state["last_wake_at"]})
        def log_message(self,fmt,*args): pass
    return Handler

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--host",default="0.0.0.0"); p.add_argument("--port",type=int,default=8787)
    p.add_argument("--mac",required=True); p.add_argument("--broadcast",required=True)
    p.add_argument("--wol-port",type=int,default=9)
    p.add_argument("--token-env",default="AZATHOTH_WAKE_RELAY_TOKEN")
    p.add_argument("--allowed-origin",action="append",default=[])
    p.add_argument("--certfile"); p.add_argument("--keyfile")
    a=p.parse_args()
    token=os.environ.get(a.token_env,"")
    if not token:
        p.error(f"{a.token_env} is required")
    handler=handler_for(token=token,mac=a.mac,broadcast=a.broadcast,port=a.wol_port,
                        allowed_origins=tuple(a.allowed_origin))
    server=ThreadingHTTPServer((a.host,a.port),handler)
    if bool(a.certfile)!=bool(a.keyfile):
        p.error("--certfile and --keyfile must be supplied together")
    if a.certfile and a.keyfile:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(a.certfile,a.keyfile)
        server.socket=context.wrap_socket(server.socket,server_side=True)
    server.serve_forever()
    return 0

if __name__=="__main__":
    raise SystemExit(main())
