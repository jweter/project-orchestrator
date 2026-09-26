#!/usr/bin/env python3
import argparse,json,mimetypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

def handler_for(state_root:Path,assets:Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=self.path.split("?",1)[0]
            if path=="/api/dashboard":
                target=state_root/"telemetry"/"foundry-dashboard.json"
                try: data=target.read_bytes()
                except OSError:
                    self.send_response(503); self.end_headers(); return
                self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
                self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data); return
            rel="index.html" if path=="/" else path.lstrip("/")
            target=(assets/rel).resolve()
            if assets.resolve() not in target.parents and target!=assets.resolve():
                self.send_response(404); self.end_headers(); return
            if not target.is_file():
                self.send_response(404); self.end_headers(); return
            data=target.read_bytes(); self.send_response(200)
            self.send_header("Content-Type",mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.end_headers(); self.wfile.write(data)
        def log_message(self,fmt,*args): pass
    return Handler

def main():
    p=argparse.ArgumentParser(); p.add_argument("--state-root",type=Path,required=True); p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",type=int,default=8765)
    a=p.parse_args(); assets=Path(__file__).resolve().parents[1]/"dashboard"
    ThreadingHTTPServer((a.host,a.port),handler_for(a.state_root,assets)).serve_forever()
if __name__=="__main__": main()
