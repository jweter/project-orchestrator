const $=s=>document.querySelector(s);const esc=s=>String(s??"—").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
function card(title,body,status=""){return '<div class="card"><div class="row"><div class="title">'+esc(title)+'</div>'+status+'</div><div class="detail">'+body+'</div></div>'}
function statusClass(s){return s==="LIVE"?"good":s==="STALE"||s==="UNKNOWN"?"warn":"bad"}
function healthName(k){return ({windows:"Windows",dashboard_snapshot:"Dashboard",foundry_supervisor:"Foundry",github_guardian:"Guardian",remote_control:"Remote control"})[k]||k}
async function refresh(){try{
  const [r,hr]=await Promise.all([fetch("/api/dashboard",{cache:"no-store"}),fetch("/api/health",{cache:"no-store"})]);
  if(!r.ok)throw Error(r.status);
  const d=await r.json(),h=hr.ok?await hr.json():null,subs=h?.subsystems||{};
  const states=Object.values(subs).map(x=>x.status);
  const overall=states.length&&states.every(s=>s==="LIVE")?"LIVE":states.some(s=>s==="OFFLINE")?"DEGRADED":"STALE";
  $("#live").textContent=overall;$("#live").className=statusClass(overall==="DEGRADED"?"OFFLINE":overall);
  $("#health").innerHTML=Object.entries(subs).map(([k,v])=>{
    let detail="";
    if(k==="dashboard_snapshot"&&Number.isFinite(v.age_seconds))detail=Math.round(v.age_seconds)+"s old";
    else if(k==="windows"&&v.boot)detail="Boot "+new Date(v.boot).toLocaleString();
    else if(k==="github_guardian")detail="Guardian "+(v.guardian?"up":"down")+" · Mailbox "+(v.mailbox_watcher?"up":"down");
    else detail=v.status||"UNKNOWN";
    return card(healthName(k),esc(detail),'<span class="status '+statusClass(v.status)+'">'+esc(v.status)+'</span>');
  }).join("")||card("Health unavailable","No subsystem health response.");
  const receipt=h?.last_control_receipt;
  if(receipt)$("#control-result").textContent="LAST "+esc(receipt.action)+" · "+esc(receipt.status)+" · "+esc(receipt.at||"");
  const c=d.outcome_counters||{},f=d.flow||{};$("#summary").innerHTML=[["Workers",d.active_count||0],["Verified",c.merged_verified||0],["Repairs",c.repairs||0],["Adv/hr",Number(f.verified_advancement_per_worker_hour||0).toFixed(2)]].map(x=>'<div class="metric"><b>'+x[1]+'</b><span>'+x[0]+'</span></div>').join("");
  const products=d.products||{};$("#products").innerHTML=Object.entries(products).map(([name,p])=>card(name,'Candidates: '+esc(p.candidate_count||0)+' · Selected: '+esc(p.selected_count||0),'<span class="status">QUEUE</span>')).join("")||card("No planner snapshot","Waiting for Foundry telemetry.");
  const grantWorkers=(d.active_work||[]).map(w=>card(w.repository||"Worker",'Slice '+esc(w.slice_id)+' · '+esc(w.runner_id),'<span class="status good">'+esc(w.state)+'</span>'));
  const poolWorkers=(d.active_worker_pool||[]).map(w=>{const s=w.activity_status||"UNKNOWN";const cls=s==="RUNNING"?"good":s==="STALE"?"warn":"";return card(w.product||"Worker",esc(w.worker_id)+' · slot '+esc(w.slot),'<span class="status '+cls+'">'+esc(s)+'</span>')});
  $("#workers").innerHTML=(grantWorkers.length?grantWorkers:poolWorkers).join("")||card("No active workers","No active grant or worker-pool activity is currently reported.");
  $("#recent").innerHTML=(d.recent_completed_work||[]).slice(0,8).map(w=>card(w.repository||"Completed",esc(w.result_status)+' · '+esc(w.outcome_quality)+(w.pr_number?' · PR #'+esc(w.pr_number):''))).join("")||card("No recent work","No terminal work in the current snapshot.");
}catch(e){$("#live").textContent="OFFLINE";$("#live").className="bad"}}refresh();setInterval(refresh,15000);
async function control(action,needsConfirm){
  if(needsConfirm&&!confirm("Reboot Azathoth Windows? The dashboard will disconnect briefly."))return;
  let token=localStorage.getItem("azathothControlToken");
  if(!token){token=prompt("Azathoth admin token");if(!token)return;localStorage.setItem("azathothControlToken",token)}
  const out=$("#control-result");out.textContent="REQUESTING "+action+"…";
  try{
    const r=await fetch("/api/control",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+token},body:JSON.stringify({action})});
    const d=await r.json();
    if(r.status===401){localStorage.removeItem("azathothControlToken")}
    out.textContent=(d.status||"UNKNOWN")+(d.command_id?" · "+d.command_id.slice(0,8):"");
  }catch(e){out.textContent="CONTROL CHANNEL OFFLINE"}
}
document.querySelectorAll("#control button").forEach(b=>b.addEventListener("click",()=>control(b.dataset.action,b.dataset.confirm==="true")));
