import argparse,json,os,socket,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
HOSTS=("1.1.1.1","8.8.8.8")
def now():return datetime.now(timezone.utc).isoformat()
def internet_ok(timeout=2):
 for h in HOSTS:
  try:
   with socket.create_connection((h,443),timeout=timeout):return True
  except OSError:pass
 return False
def run(c):return subprocess.run(c,capture_output=True,text=True,timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
def human_mode(r):return (r/"human-mode.json").exists() or (r/"give-me-control").exists()
def log(r,e,**d):
 r.mkdir(parents=True,exist_ok=True)
 with (r/"network-recovery.jsonl").open("a",encoding="utf8") as f:f.write(json.dumps({"ts":now(),"event":e,**d},sort_keys=True)+"\n")
def snapshot(r):
 out={}
 for n,c in {"wlan":["netsh","wlan","show","interfaces"],"ip":["ipconfig","/all"],"route":["route","print","-4"]}.items():
  try:
   x=run(c);out[n]={"rc":x.returncode,"stdout":x.stdout[-12000:],"stderr":x.stderr[-2000:]}
  except Exception as e:out[n]={"error":repr(e)}
 q=r/"network-incidents";q.mkdir(parents=True,exist_ok=True);(q/(datetime.now().strftime("%Y%m%d-%H%M%S")+".json")).write_text(json.dumps({"ts":now(),**out},indent=2))
def recover(r,allow_reboot=False):
 snapshot(r)
 if human_mode(r):log(r,"RECOVERY_SUPPRESSED_HUMAN_MODE");return False
 steps=[("wlan_reconnect",["netsh","wlan","connect","name=ATTgw6SIjk"]),("wlan_service_restart",["powershell","-NoProfile","-Command","Restart-Service WlanSvc -Force"]),("adapter_restart",["powershell","-NoProfile","-Command","Get-NetAdapter -InterfaceDescription 'Intel(R) Wi-Fi 6E AX211 160MHz' | Restart-NetAdapter -Confirm:$false"])]
 for n,c in steps:
  try:x=run(c);log(r,n,rc=x.returncode,stderr=x.stderr[-1000:])
  except Exception as e:log(r,n,error=repr(e))
  time.sleep(8)
  if internet_ok():log(r,"RECOVERED",step=n);return True
 log(r,"RECOVERY_EXHAUSTED")
 if allow_reboot and not human_mode(r):log(r,"BOUNDED_REBOOT_REQUESTED");run(["shutdown","/r","/t","15","/c","Azathoth network recovery exhausted; rebooting to restore connectivity"])
 return False
def main():
 a=argparse.ArgumentParser();a.add_argument("--state-root",type=Path,required=True);a.add_argument("--poll-seconds",type=int,default=30);a.add_argument("--failures",type=int,default=3);a.add_argument("--allow-reboot",action="store_true");a.add_argument("--once",action="store_true");q=a.parse_args();bad=0;log(q.state_root,"GUARDIAN_STARTED",pid=os.getpid())
 while True:
  ok=internet_ok();bad=0 if ok else bad+1
  if not ok:log(q.state_root,"PROBE_FAILED",consecutive=bad)
  if bad>=q.failures:recover(q.state_root,q.allow_reboot);bad=0
  if q.once:break
  time.sleep(max(10,q.poll_seconds))
if __name__=="__main__":main()
