const $=s=>document.querySelector(s);
const esc=s=>String(s??"—").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const labelAction=a=>({STATUS_SNAPSHOT:"Status refresh",RESTART_REMOTE_CONTROL:"Remote restart",RECOVER_CONTROL_PLANE:"Control recovery",REBOOT_WINDOWS:"Windows reboot"})[a]||a;
const fmtTime=v=>{if(!v)return "—";const d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString([], {month:"short",day:"numeric",hour:"numeric",minute:"2-digit",second:"2-digit"})};
const fmtAge=s=>!Number.isFinite(s)?"Unknown":s<60?Math.round(s)+" sec ago":s<3600?Math.round(s/60)+" min ago":Math.round(s/3600)+" hr ago";
function statusClass(s){return s==="LIVE"||s==="COMPLETED"?"good":s==="STALE"||s==="UNKNOWN"||s==="REQUESTED"?"warn":"bad"}
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
function renderReceipt(receipt){
  if(!receipt)return;
  $("#control-result").innerHTML='<b class="'+statusClass(receipt.status)+'">'+esc(receipt.status)+'</b> · '+esc(labelAction(receipt.action))+'<br><span class="muted">'+esc(fmtTime(receipt.at))+'</span>';
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
    $("#hero-detail").textContent=overall==="LIVE"?"All control channels are online":overall==="DEGRADED"?"One or more control channels are offline":"Waiting for fresh subsystem data";
    $("#last-refresh").textContent="Updated "+new Date().toLocaleTimeString([], {hour:"numeric",minute:"2-digit"});
    renderHealth(subs);renderReceipt(h?.last_control_receipt);
    const c=d.outcome_counters||{},f=d.flow||{};
    $("#summary").innerHTML=[["Workers",d.active_count||0],["Verified",c.merged_verified||0],["Repairs",c.repairs||0],["Adv/hr",Number(f.verified_advancement_per_worker_hour||0).toFixed(2)]].map(x=>'<div class="metric"><b>'+esc(x[1])+'</b><span>'+esc(x[0])+'</span></div>').join("");
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
  const out=$("#control-result");out.innerHTML='<b class="warn">REQUESTING</b> · '+esc(labelAction(action));
  try{
    const r=await fetch("/api/control",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+token},body:JSON.stringify({action})});
    const d=await r.json();
    if(r.status===401){localStorage.removeItem("azathothControlToken");out.innerHTML='<b class="bad">TOKEN EXPIRED</b> · Tap the control again';return}
    out.innerHTML='<b class="'+statusClass(d.status)+'">'+esc(d.status||"UNKNOWN")+'</b> · '+esc(labelAction(action))+(d.command_id?'<br><span class="muted">Command '+esc(d.command_id.slice(0,8))+'</span>':'');
    setTimeout(refresh,2500);
  }catch(e){out.innerHTML='<b class="bad">OFFLINE</b> · Control channel unavailable'}
}
document.querySelectorAll("#control button").forEach(b=>b.addEventListener("click",()=>control(b.dataset.action,b.dataset.confirm==="true")));
$("#refresh-now").addEventListener("click",refresh);
refresh();setInterval(refresh,15000);
