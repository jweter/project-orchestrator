#!/usr/bin/env python3
import argparse,json,mimetypes,os,secrets,subprocess,uuid
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

def _read_json(path:Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        return None

def _health_snapshot(state_root:Path,control_repo:Path|None)->dict:
    now=datetime.now(timezone.utc)
    dashboard=_read_json(state_root/"telemetry"/"foundry-dashboard.json") or {}
    recorded=dashboard.get("recorded_at")
    age_seconds=None
    if isinstance(recorded,str):
        try:
            stamp=datetime.fromisoformat(recorded.replace("Z","+00:00"))
            if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=timezone.utc)
            age_seconds=max((now-stamp.astimezone(timezone.utc)).total_seconds(),0.0)
        except ValueError:
            pass
    snapshot_status="LIVE" if age_seconds is not None and age_seconds<=120 else "STALE" if age_seconds is not None else "UNKNOWN"
    processes={"supervisor":False,"guardian":False,"mailbox_watcher":False,"remote_control":False,"boot":None}
    if os.name=="nt":
        ps_script="""$p=Get-CimInstance Win32_Process; [ordered]@{
supervisor=[bool]($p|Where-Object{$_.CommandLine -like '*continuous_flow_supervisor.py*'}|Select-Object -First 1)
guardian=[bool]($p|Where-Object{$_.CommandLine -like '*Guard-GitHubMailbox.ps1*'}|Select-Object -First 1)
mailbox_watcher=[bool]($p|Where-Object{$_.CommandLine -like '*Watch-GitHubMailbox.ps1*'}|Select-Object -First 1)
remote_control=[bool]($p|Where-Object{$_.Name -eq 'node.exe' -and $_.CommandLine -match 'desktop-commander' -and $_.CommandLine -match 'remote'}|Select-Object -First 1)
boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
}|ConvertTo-Json -Compress"""
        try:
            probe=subprocess.run(["powershell.exe","-NoProfile","-Command",ps_script],capture_output=True,text=True,timeout=8,check=True)
            processes.update(json.loads(probe.stdout))
        except (OSError,subprocess.SubprocessError,json.JSONDecodeError,TypeError):
            pass
    receipt=_read_json(control_repo/"control"/"receipt.json") if control_repo else None
    def state(value): return "LIVE" if value else "OFFLINE"
    return {
        "recorded_at":now.isoformat(),
        "subsystems":{
            "windows":{"status":"LIVE" if os.name=="nt" else "UNKNOWN","boot":processes.get("boot")},
            "dashboard_snapshot":{"status":snapshot_status,"recorded_at":recorded,"age_seconds":age_seconds},
            "foundry_supervisor":{"status":state(processes.get("supervisor")) if os.name=="nt" else "UNKNOWN"},
            "github_guardian":{"status":state(processes.get("guardian") and processes.get("mailbox_watcher")) if os.name=="nt" else "UNKNOWN",
                               "guardian":bool(processes.get("guardian")),"mailbox_watcher":bool(processes.get("mailbox_watcher"))},
            "remote_control":{"status":state(processes.get("remote_control")) if os.name=="nt" else "UNKNOWN"},
        },
        "last_control_receipt":receipt,
    }

def handler_for(state_root:Path,assets:Path,control_repo:Path|None=None,control_token:str|None=None):
    class Handler(BaseHTTPRequestHandler):
        def _json(self,status,payload):
            data=json.dumps(payload,sort_keys=True).encode()
            self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            if self.path.split("?",1)[0]!="/api/control":
                self.send_response(404); self.end_headers(); return
            supplied=self.headers.get("Authorization","").removeprefix("Bearer ").strip()
            if not control_token or not supplied or not secrets.compare_digest(supplied,control_token):
                self._json(401,{"status":"UNAUTHORIZED"}); return
            if control_repo is None:
                self._json(503,{"status":"CONTROL_REPO_UNAVAILABLE"}); return
            try:
                length=int(self.headers.get("Content-Length","0"))
                body=json.loads(self.rfile.read(min(length,4096)) or b"{}")
            except (ValueError,json.JSONDecodeError):
                self._json(400,{"status":"INVALID_JSON"}); return
            allowed={"STATUS_SNAPSHOT","RESTART_REMOTE_CONTROL","RECOVER_CONTROL_PLANE","REBOOT_WINDOWS"}
            action=body.get("action")
            if action not in allowed:
                self._json(400,{"status":"REJECTED_ACTION"}); return
            command_id=str(uuid.uuid4())
            now=datetime.now(timezone.utc)
            mailbox={"command_id":command_id,"action":action,"issued_at":now.isoformat(),"expires_at":(now+timedelta(minutes=5)).isoformat(),"source":"iphone-dashboard"}
            target=control_repo/"control"/"mailbox.json"
            try:
                target.write_text(json.dumps(mailbox,indent=2,sort_keys=True)+"\
",encoding="utf-8")
                subprocess.run(["git","-C",str(control_repo),"add","control/mailbox.json"],check=True,capture_output=True)
                subprocess.run(["git","-C",str(control_repo),"commit","-m",f"control: {action.lower()} {command_id}"],check=True,capture_output=True)
                subprocess.run(["git","-C",str(control_repo),"push","origin","azathoth-control"],check=True,capture_output=True)
            except (OSError,subprocess.CalledProcessError):
                self._json(503,{"status":"DISPATCH_FAILED","command_id":command_id}); return
            self._json(202,{"status":"REQUESTED","command_id":command_id,"action":action})
        def do_GET(self):
            path=self.path.split("?",1)[0]
            if path=="/api/health":
                self._json(200,_health_snapshot(state_root,control_repo)); return
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
    p=argparse.ArgumentParser(); p.add_argument("--state-root",type=Path,required=True); p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",type=int,default=8765); p.add_argument("--control-repo",type=Path); p.add_argument("--control-token-env",default="AZATHOTH_DASHBOARD_CONTROL_TOKEN")
    a=p.parse_args(); assets=Path(__file__).resolve().parents[1]/"dashboard"
    token=os.environ.get(a.control_token_env); ThreadingHTTPServer((a.host,a.port),handler_for(a.state_root,assets,a.control_repo,token)).serve_forever()
if __name__=="__main__": main()
