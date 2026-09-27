const $=s=>document.querySelector(s);
const esc=s=>String(s??"—").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const labels={STATUS_SNAPSHOT:"Status refresh",RESTART_REMOTE_CONTROL:"Remote restart",RECOVER_CONTROL_PLANE:"Control recovery",REBOOT_WINDOWS:"Windows reboot",HUMAN_MODE:"Human Mode",FULL_POWER:"Full Power"};
const terminal=new Set(["COMPLETED","REBOOT_ACCEPTED","REJECTED_EXPIRED","REJECTED_ACTION","DISPATCH_FAILED","REMOTE_TASK_NOT_FOUND","SYNC_FETCH_FAILED","SYNC_CHECKOUT_FAILED","SYNC_RESET_FAILED","REJECTED_ACTOR_REPO_DIRTY"]);
let activeCommand=null;
const fmtTime=v=>{if(!v)return "—";const d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString([], {month:"short",day:"numeric",hour:"numeric",minute:"2-digit",second:"2-digit"})};
const fmtAge=s=>!Number.isFinite(s)?"Unknown":s<60?Math.round(s)+" sec ago":s<3600?Math.round(s/60)+" min ago":Math.round(s/3600)+" hr ago";
const statusClass=s=>s==="LIVE"||s==="COMPLETED"?"good":s==="STALE"||s==="UNKNOWN"||s==="REQUESTED"||s==="ACKNOWLEDGED"||s==="EXECUTING"?"warn":"bad";
const actionLabel=a=>labels[a]||a||"Command";
function card(title,body,status=""){return '<div class="card"><div class="row"><div class="title">'+esc(title)+'</div>'+status+'</div><div class="detail">'+body+'</div></div>'}
function healthName(k){return ({windows:"Windows",dashboard_snapshot:"Dashboard",foundry_supervisor:"Foundry",github_guardian:"Guardian",remote_control:"Remote"})[k]||k}
function healthDetail(k,v){
  if(k==="dashboard_snapshot")return fmtAge(v.age_seconds);
  if(k==="windows"&&v.boot)return "Up since "+fmtTime(v.boot);
  if(k==="github_guardian")return (v.guardian?"Guardian online":"Guardian down")+" · "+(v.mailbox_watcher?"Mailbox online":"Mailbox down");
  if(k==="foundry_supervisor")return v.status==="LIVE"?"Supervisor running":"Supervisor unavailable";
  if(k==="remote_control")return v.status==="LIVE"?"Direct control connected":"Direct control unavailable";
  return v.status||"Unknown";
}
function renderHealth(subs){
  $("#health").innerHTML=Object.entries(subs).map(([k,v])=>
    '<div class="health-card"><div class="health-top"><b>'+esc(healthName(k))+'</b><span class="status '+statusClass(v.status)+'">'+esc(v.status)+'</span></div><div class="health-detail">'+esc(healthDetail(k,v))+'</div></div>'
  ).join("")||'<div class="health-card"><b>Health unavailable</b><div class="health-detail">Waiting for subsystem telemetry.</div></div>';
}
function metric(value,label,extra=""){return '<div class="metric"><b>'+esc(value)+'</b><span>'+esc(label)+'</span>'+(extra?'<small>'+esc(extra)+'</small>':'')+'</div>'}
function renderMachine(m={}){
  const cpu=Number.isFinite(m.cpu_percent)?Math.round(m.cpu_percent)+"%":"—";
  const ram=Number.isFinite(m.memory_percent)?Math.round(m.memory_percent)+"%":"—";
  const disk=Number.isFinite(m.disk_free_gb)?m.disk_free_gb.toFixed(0)+" GB":"—";
  let power="AC";
  if(Number.isFinite(m.battery_percent))power=m.battery_percent+"%";
  const wake=m.wake_armed?"Armed":m.wake_magic_enabled?"Setup":"No";
  const wakeExtra=m.wake_armed?"External relay still required":m.wake_magic_enabled?"Magic Packet supported; Windows not armed":"No wake capability detected";
  $("#machine").innerHTML=metric(cpu,"CPU")+metric(ram,"RAM",Number.isFinite(m.memory_used_gb)?m.memory_used_gb+" / "+m.memory_total_gb+" GB":"")+metric(disk,"Disk free")+metric(power,"Battery/Power",m.pending_reboot?"Reboot pending":"")+metric(wake,"Wake",wakeExtra);
}
function renderAlerts(alerts=[]){
  $("#alerts").innerHTML=alerts.map(a=>'<div class="alert '+(a.severity==="CRITICAL"?"alert-bad":"alert-warn")+'"><b>'+esc(a.severity)+'</b><span>'+esc(a.message)+'</span></div>').join("");
}
function renderMode(mode={}){
  const on=mode.enabled===true;
  $("#mode-label").textContent=on?"Human Mode active":"Full Power active";
  document.querySelectorAll("#mode-control button").forEach(b=>b.classList.toggle("selected",(on&&b.dataset.action==="HUMAN_MODE")||(!on&&b.dataset.action==="FULL_POWER")));
}
function renderProgress(h){
  const p=h?.command_progress,receipt=h?.last_control_receipt;
  let row=p;
  if(activeCommand&&p?.command_id!==activeCommand&&receipt?.command_id===activeCommand)row=receipt;
  if(activeCommand&&row?.command_id!==activeCommand)return;
  if(!row)row=receipt;
  if(!row)return;
  $("#control-result").innerHTML='<div class="progress-row"><b class="'+statusClass(row.status)+'">'+esc(row.status)+'</b><span>'+esc(actionLabel(row.action))+'</span></div><span class="muted">'+esc(fmtTime(row.at))+(row.command_id?" · "+esc(row.command_id.slice(0,8)):"")+'</span>';
  if(activeCommand===row.command_id&&terminal.has(row.status))activeCommand=null;
}
function renderHistory(rows=[]){
  const list=[...rows].reverse().slice(0,10);
  $("#history").innerHTML=list.map(r=>'<div class="history-row"><div><b>'+esc(actionLabel(r.action))+'</b><span>'+esc(fmtTime(r.at))+'</span></div><span class="status '+statusClass(r.status)+'">'+esc(r.status)+'</span></div>').join("")||'<div class="command-card muted">No phone commands recorded yet.</div>';
}
async function refresh(){
  try{
    const [r,hr]=await Promise.all([fetch("/api/dashboard",{cache:"no-store"}),fetch("/api/health",{cache:"no-store"})]);
    if(!r.ok)throw Error(r.status);
    const d=await r.json(),h=hr.ok?await hr.json():null,subs=h?.subsystems||{};
    const states=Object.values(subs).map(x=>x.status);
    const overall=states.length&&states.every(s=>s==="LIVE")?"LIVE":states.some(s=>s==="OFFLINE")?"DEGRADED":"STALE";
    $("#live").textContent=overall;$("#live").className="pill "+statusClass(overall==="DEGRADED"?"OFFLINE":overall);
    $("#hero-state").textContent=overall==="LIVE"?"Azathoth is healthy":overall==="DEGRADED"?"Azathoth needs attention":"Telemetry is stale";
    const alertCount=h?.alerts?.length||0;
    $("#hero-detail").textContent=alertCount?alertCount+" active alert"+(alertCount===1?"":"s"):overall==="LIVE"?"All control channels are online":"Waiting for fresh subsystem data";
    $("#last-refresh").textContent="Updated "+new Date().toLocaleTimeString([], {hour:"numeric",minute:"2-digit"});
    renderAlerts(h?.alerts||[]);renderMode(h?.human_mode||{});renderHealth(subs);renderMachine(h?.machine||{});renderProgress(h);renderHistory(h?.control_history||[]);
    const c=d.outcome_counters||{},f=d.flow||{};
    $("#summary").innerHTML=[["Workers",d.active_count||0],["Verified",c.merged_verified||0],["Repairs",c.repairs||0],["Adv/hr",Number(f.verified_advancement_per_worker_hour||0).toFixed(2)]].map(x=>metric(x[1],x[0])).join("");
    const products=d.products||{};
    $("#products").innerHTML=Object.entries(products).map(([name,p])=>card(name,'Candidates '+esc(p.candidate_count||0)+' · Selected '+esc(p.selected_count||0),'<span class="status">QUEUE</span>')).join("")||card("No planner snapshot","Waiting for Foundry telemetry.");
    const grantWorkers=(d.active_work||[]).map(w=>card(w.repository||"Worker",'Slice '+esc(w.slice_id)+' · '+esc(w.runner_id),'<span class="status good">'+esc(w.state)+'</span>'));
    const poolWorkers=(d.active_worker_pool||[]).map(w=>{const s=w.activity_status||"UNKNOWN";return card(w.product||"Worker",esc(w.worker_id)+' · slot '+esc(w.slot),'<span class="status '+statusClass(s==="RUNNING"?"LIVE":s)+'">'+esc(s)+'</span>')});
    $("#workers").innerHTML=(grantWorkers.length?grantWorkers:poolWorkers).join("")||card("No active workers","No active grant or worker-pool activity is currently reported.");
    $("#recent").innerHTML=(d.recent_completed_work||[]).slice(0,6).map(w=>card(w.repository||"Completed",esc(w.result_status)+' · '+esc(w.outcome_quality)+(w.pr_number?' · PR #'+esc(w.pr_number):''))).join("")||card("No recent work","No terminal work in the current snapshot.");
  }catch(e){
    $("#live").textContent="OFFLINE";$("#live").className="pill bad";
    $("#hero-state").textContent="Dashboard offline";$("#hero-detail").textContent="Unable to reach Azathoth telemetry";
  }
}
async function control(action,needsConfirm){
  if(needsConfirm&&!confirm("Reboot Azathoth Windows? The dashboard and remote control will disconnect briefly."))return;
  let token=localStorage.getItem("azathothControlToken");
  if(!token){token=prompt("Azathoth admin token");if(!token)return;localStorage.setItem("azathothControlToken",token)}
  $("#control-result").innerHTML='<b class="warn">REQUESTING</b> · '+esc(actionLabel(action));
  try{
    const r=await fetch("/api/control",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+token},body:JSON.stringify({action})});
    const d=await r.json();
    if(r.status===401){localStorage.removeItem("azathothControlToken");$("#control-result").innerHTML='<b class="bad">TOKEN INVALID</b> · Tap again to authenticate';return}
    activeCommand=d.command_id||null;
    $("#control-result").innerHTML='<b class="'+statusClass(d.status)+'">'+esc(d.status||"UNKNOWN")+'</b> · '+esc(actionLabel(action));
    pollCommand();
  }catch(e){$("#control-result").innerHTML='<b class="bad">OFFLINE</b> · Control channel unavailable'}
}
async function pollCommand(){
  if(!activeCommand)return;
  await new Promise(r=>setTimeout(r,1500));
  await refresh();
  if(activeCommand)pollCommand();
}
document.querySelectorAll("#control button,#mode-control button").forEach(b=>b.addEventListener("click",()=>control(b.dataset.action,b.dataset.confirm==="true")));
$("#refresh-now").addEventListener("click",refresh);
$("#auth-settings").addEventListener("click",()=>{
  const has=!!localStorage.getItem("azathothControlToken");
  if(has&&confirm("Forget the saved Azathoth admin token on this iPhone?")){localStorage.removeItem("azathothControlToken");alert("Token forgotten. The next control will ask for it again.");}
  else if(!has)alert("No admin token is currently saved on this iPhone. Tap a control to authenticate.");
});
if("serviceWorker" in navigator)navigator.serviceWorker.register("/sw.js").catch(()=>{});
const standalone=window.matchMedia("(display-mode: standalone)").matches||window.navigator.standalone===true;
$("#install-card").classList.toggle("hidden",standalone);
refresh();setInterval(refresh,15000);
