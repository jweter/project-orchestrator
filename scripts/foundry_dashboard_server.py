#!/usr/bin/env python3
import argparse,json,mimetypes,os,secrets,subprocess,sys,time,uuid
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

_ROOT=Path(__file__).resolve().parents[1]
for _p in (str(_ROOT/"src"),str(_ROOT)):
    if _p not in sys.path: sys.path.insert(0,_p)

def _execution_view(state_root:Path)->dict:
    """What Iron God is doing right now (model, task, phase, worker, memory, why it waits). Unknown stays unknown."""
    try:
        import urllib.request
        from project_orchestrator import iron_god_execution_status as es
        from project_orchestrator import iron_god_local_model as lm
        from project_orchestrator.iron_god_calibration import load_profile
        loaded=None
        try:
            with urllib.request.urlopen(os.environ.get("OLLAMA_API_BASE","http://127.0.0.1:11434").rstrip("/")+"/api/ps",timeout=3) as r:
                loaded=[str(m.get("name") or m.get("model")) for m in json.load(r).get("models",[])]
        except (OSError,ValueError): pass
        try: free=lm.available_gib()
        except Exception: free=None
        authorized=None
        try:
            from project_orchestrator.human_ownership import AUTHORIZED,owner_control_status
            authorized=owner_control_status(state_root)==AUTHORIZED
        except Exception: pass
        return es.view(state_root/"iron-god-products",loaded_models=loaded,available_gib=free,
                       capability_profile=load_profile(state_root),owner_authorized=authorized,
                       builder_model=lm.default_ollama_model())
    except Exception as exc:
        return {"phase":"UNKNOWN","detail":"execution view unavailable: "+type(exc).__name__,"active":False}

def _read_json(path:Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        return None

def _write_json(path:Path,payload:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    tmp.replace(path)

def _fallback_root(state_root:Path)->Path:
    return state_root.parent/"FallbackControl"

def _invoke_local_mode(action:str,state_root:Path)->dict:
    scripts={
        "HUMAN_MODE":Path(__file__).resolve().parent/"enter_human_mode.ps1",
        "FULL_POWER":Path(__file__).resolve().parent/"start_full_power.ps1",
    }
    script=scripts.get(action)
    if script is None:
        return {"status":"REJECTED_ACTION"}
    if os.name!="nt":
        return {"status":"LOCAL_CONTROL_UNAVAILABLE"}
    if not script.is_file():
        return {"status":"LOCAL_CONTROL_SCRIPT_MISSING"}
    try:
        p=subprocess.run(
            ["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden",
             "-File",str(script),"-StateRoot",str(state_root)],
            capture_output=True,text=True,timeout=20,check=False,
        )
    except (OSError,subprocess.SubprocessError):
        return {"status":"LOCAL_CONTROL_FAILED"}
    return {"status":"COMPLETED" if p.returncode==0 else "LOCAL_CONTROL_FAILED",
            "returncode":p.returncode}

_MACHINE_CACHE={"at":0.0,"value":None}
def _machine_probe()->dict:
    if _MACHINE_CACHE["value"] is not None and time.monotonic()-_MACHINE_CACHE["at"]<10:
        return dict(_MACHINE_CACHE["value"])
    default={"cpu_percent":None,"memory_percent":None,"memory_used_gb":None,"memory_total_gb":None,
             "disk_free_gb":None,"disk_total_gb":None,"battery_percent":None,"battery_status":None,
             "pending_reboot":None,"boot":None,"wake_magic_enabled":None,"wake_armed":None,
             "wake_external_relay_required":True}
    if os.name!="nt":
        return default
    script=r"""$os=Get-CimInstance Win32_OperatingSystem
$cpu=(Get-CimInstance Win32_Processor|Measure-Object -Property LoadPercentage -Average).Average
$disk=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$b=Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue|Select-Object -First 1
$wifi=Get-NetAdapter -Physical -ErrorAction SilentlyContinue|Where-Object{$_.Status -eq 'Up'}|Select-Object -First 1
$wakeMagic=$false
$wakeArmed=$false
if($wifi){
  $wakeProp=Get-NetAdapterAdvancedProperty -Name $wifi.Name -ErrorAction SilentlyContinue|Where-Object{$_.DisplayName -eq 'Wake on Magic Packet'}|Select-Object -First 1
  $wakeMagic=[bool]($wakeProp -and $wakeProp.DisplayValue -eq 'Enabled')
  $armedText=(powercfg /devicequery wake_armed 2>$null)-join "\n"
  $wakeArmed=[bool]($armedText -match [regex]::Escape($wifi.InterfaceDescription) -or $armedText -match [regex]::Escape($wifi.Name))
}
$pending=(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
         (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
         (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\PendingFileRenameOperations')
$used=[double]$os.TotalVisibleMemorySize-[double]$os.FreePhysicalMemory
[ordered]@{
 cpu_percent=[math]::Round([double]$cpu,1)
 memory_percent=[math]::Round(($used/[double]$os.TotalVisibleMemorySize)*100,1)
 memory_used_gb=[math]::Round($used/1MB,1)
 memory_total_gb=[math]::Round([double]$os.TotalVisibleMemorySize/1MB,1)
 disk_free_gb=[math]::Round([double]$disk.FreeSpace/1GB,1)
 disk_total_gb=[math]::Round([double]$disk.Size/1GB,1)
 battery_percent=if($b){[int]$b.EstimatedChargeRemaining}else{$null}
 battery_status=if($b){[int]$b.BatteryStatus}else{$null}
 pending_reboot=[bool]$pending
 boot=$os.LastBootUpTime.ToUniversalTime().ToString('o')
 wake_magic_enabled=[bool]$wakeMagic
 wake_armed=[bool]$wakeArmed
 wake_external_relay_required=$true
}|ConvertTo-Json -Compress"""
    try:
        p=subprocess.run(["powershell.exe","-NoProfile","-Command",script],capture_output=True,text=True,timeout=8,check=True)
        row=json.loads(p.stdout)
        default.update(row)
    except (OSError,subprocess.SubprocessError,json.JSONDecodeError,TypeError,ValueError):
        pass
    _MACHINE_CACHE["at"]=time.monotonic(); _MACHINE_CACHE["value"]=dict(default)
    return default

CONTROL_PLANE_SUBSYSTEMS=("windows","foundry_supervisor","github_guardian","remote_control")

def _overall_control_status(subsystems:dict)->str:
    states=[subsystems.get(name,{}).get("status","UNKNOWN") for name in CONTROL_PLANE_SUBSYSTEMS]
    if any(status=="OFFLINE" for status in states):
        return "DEGRADED"
    if states and all(status=="LIVE" for status in states):
        return "LIVE"
    return "CHECKING"

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
    processes={"supervisor":False,"guardian":False,"mailbox_watcher":False,"remote_control":False}
    if os.name=="nt":
        probe_script=r"""$p=Get-CimInstance Win32_Process; [ordered]@{
supervisor=[bool]($p|Where-Object{$_.CommandLine -like '*continuous_flow_supervisor.py*'}|Select-Object -First 1)
guardian=[bool]($p|Where-Object{$_.CommandLine -like '*Guard-GitHubMailbox.ps1*'}|Select-Object -First 1)
mailbox_watcher=[bool]($p|Where-Object{$_.CommandLine -like '*Watch-GitHubMailbox.ps1*'}|Select-Object -First 1)
remote_control=[bool]($p|Where-Object{$_.Name -eq 'node.exe' -and $_.CommandLine -match 'desktop-commander' -and $_.CommandLine -match 'remote'}|Select-Object -First 1)
}|ConvertTo-Json -Compress"""
        try:
            p=subprocess.run(["powershell.exe","-NoProfile","-Command",probe_script],capture_output=True,text=True,timeout=8,check=True)
            processes.update(json.loads(p.stdout))
        except (OSError,subprocess.SubprocessError,json.JSONDecodeError,TypeError,ValueError):
            pass
    machine=_machine_probe()
    fallback=_fallback_root(state_root)
    receipt=_read_json(control_repo/"control"/"receipt.json") if control_repo else None
    progress=_read_json(fallback/"progress.json")
    history=_read_json(fallback/"history.json") or {"commands":[]}
    human=_read_json(state_root/"human-mode.json") or {"enabled":False}
    def state(value): return "LIVE" if value else "OFFLINE"
    subsystems={
        "windows":{"status":"LIVE" if os.name=="nt" else "UNKNOWN","boot":machine.get("boot")},
        "dashboard_snapshot":{"status":snapshot_status,"recorded_at":recorded,"age_seconds":age_seconds},
        "foundry_supervisor":{"status":state(processes.get("supervisor")) if os.name=="nt" else "UNKNOWN"},
        "github_guardian":{"status":state(processes.get("guardian") and processes.get("mailbox_watcher")) if os.name=="nt" else "UNKNOWN",
                           "guardian":bool(processes.get("guardian")),"mailbox_watcher":bool(processes.get("mailbox_watcher"))},
        "remote_control":{"status":state(processes.get("remote_control")) if os.name=="nt" else "UNKNOWN"},
    }
    overall_status=_overall_control_status(subsystems)
    alerts=[]
    for name,row in subsystems.items():
        if row["status"]=="OFFLINE":
            alerts.append({"severity":"CRITICAL","code":name.upper()+"_OFFLINE","message":name.replace("_"," ").title()+" is offline"})
        elif row["status"]=="STALE":
            severity="INFO" if name=="dashboard_snapshot" else "WARNING"
            alerts.append({"severity":severity,"code":name.upper()+"_STALE","message":name.replace("_"," ").title()+" telemetry is stale"})
    if machine.get("pending_reboot") is True:
        alerts.append({"severity":"WARNING","code":"WINDOWS_REBOOT_PENDING","message":"Windows is waiting for a reboot"})
    if isinstance(machine.get("disk_free_gb"),(int,float)) and machine["disk_free_gb"]<15:
        alerts.append({"severity":"WARNING","code":"DISK_LOW","message":f"C: has {machine['disk_free_gb']:.1f} GB free"})
    if isinstance(machine.get("memory_percent"),(int,float)) and machine["memory_percent"]>=90:
        alerts.append({"severity":"WARNING","code":"MEMORY_HIGH","message":f"Memory usage is {machine['memory_percent']:.0f}%"})
    return {"recorded_at":now.isoformat(),"overall_status":overall_status,
            "subsystems":subsystems,"machine":machine,"alerts":alerts,
            "human_mode":{"enabled":bool(human.get("enabled")),"since":human.get("since")},
            "command_progress":progress,"control_history":list(history.get("commands",[]))[-20:],
            "last_control_receipt":receipt,"execution":_execution_view(state_root)}

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
            try:
                length=int(self.headers.get("Content-Length","0"))
                body=json.loads(self.rfile.read(min(length,4096)) or b"{}")
            except (ValueError,json.JSONDecodeError):
                self._json(400,{"status":"INVALID_JSON"}); return
            allowed={"STATUS_SNAPSHOT","RESTART_REMOTE_CONTROL","RECOVER_CONTROL_PLANE","REBOOT_WINDOWS","HUMAN_MODE","FULL_POWER"}
            action=body.get("action")
            if action not in allowed:
                self._json(400,{"status":"REJECTED_ACTION"}); return
            command_id=str(uuid.uuid4()); now=datetime.now(timezone.utc)
            if action in {"HUMAN_MODE","FULL_POWER"}:
                result=_invoke_local_mode(action,state_root)
                status=str(result.get("status","LOCAL_CONTROL_FAILED"))
                _write_json(_fallback_root(state_root)/"progress.json",
                            {"command_id":command_id,"action":action,"status":status,"at":now.isoformat()})
                self._json(200 if status=="COMPLETED" else 503,
                           {"status":status,"command_id":command_id,"action":action})
                return
            if control_repo is None:
                self._json(503,{"status":"CONTROL_REPO_UNAVAILABLE"}); return
            mailbox={"command_id":command_id,"action":action,"issued_at":now.isoformat(),
                     "expires_at":(now+timedelta(minutes=5)).isoformat(),"source":"iphone-dashboard"}
            try:
                _write_json(_fallback_root(state_root)/"progress.json",
                            {"command_id":command_id,"action":action,"status":"REQUESTED","at":now.isoformat()})
                target=control_repo/"control"/"mailbox.json"
                target.write_text(json.dumps(mailbox,indent=2,sort_keys=True)+"\n",encoding="utf-8")
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
    p=argparse.ArgumentParser(); p.add_argument("--state-root",type=Path,required=True)
    p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",type=int,default=8765)
    p.add_argument("--control-repo",type=Path); p.add_argument("--control-token-env",default="AZATHOTH_DASHBOARD_CONTROL_TOKEN")
    a=p.parse_args(); assets=Path(__file__).resolve().parents[1]/"dashboard"
    token=os.environ.get(a.control_token_env)
    ThreadingHTTPServer((a.host,a.port),handler_for(a.state_root,assets,a.control_repo,token)).serve_forever()
if __name__=="__main__": main()
