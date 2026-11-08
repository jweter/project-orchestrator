// IRON GOD command center. READ-ONLY: it GETs /api/command-center and renders it. It never sends commands.
"use strict";
const REFRESH_MS = 5000;
let lastOk = 0;

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const $ = (id) => document.getElementById(id);
const unknown = (v, suffix = "") => (v === null || v === undefined ? '<span class="sub">unknown</span>' : esc(v) + suffix);
const clock = (iso) => { if (!iso) return ""; const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}); };
const ago = (seconds) => {
  if (seconds === null || seconds === undefined) return "unknown";
  const s = Math.round(seconds); if (s < 90) return s + " s ago"; const m = Math.round(s / 60); if (m < 90) return m + " min ago";
  return Math.round(m / 60) + " h ago";
};
const repo = (r) => esc(String(r ?? "").split("/").pop());
const badge = (evidence) => `<span class="badge ${esc(String(evidence).toLowerCase())}">${esc(evidence)}</span>`;
const bar = (used, total) => {
  if (used == null || !total) return "";
  const pct = Math.max(0, Math.min(100, Math.round((used / total) * 100)));
  return `<div class="bar ${pct > 90 ? "bad" : pct > 75 ? "warn" : ""}"><span style="width:${pct}%"></span></div>`;
};

function renderVerdict(v, engine) {
  const box = $("verdict");
  box.className = "verdict " + String(v?.state || "unknown").toLowerCase();
  $("verdict-headline").textContent = v?.headline || "Unknown";
  $("verdict-reason").textContent = v?.reason || "";
  const mode = $("mode");
  mode.textContent = engine?.mode === "TAKE_THE_WHEEL" ? "Take the Wheel" : engine?.mode === "OWNER_CONTROL" ? "You hold control" : "Control unknown";
  mode.className = "pill " + (engine?.mode === "TAKE_THE_WHEEL" ? "on" : "off");
}

function renderNow(w) {
  if (!w) { $("now").innerHTML = unknown(null); return; }
  const rows = [];
  if (w.repository) rows.push(`<dt>Repository</dt><dd>${repo(w.repository)}</dd>`);
  if (w.task) rows.push(`<dt>Task</dt><dd>${esc(w.task)}</dd>`);
  if (w.title) rows.push(`<dt>Objective</dt><dd>${esc(w.title)}</dd>`);
  if (w.model) rows.push(`<dt>Model</dt><dd>${esc(w.model)}</dd>`);
  if (w.worker_pid) rows.push(`<dt>Worker</dt><dd>pid ${esc(w.worker_pid)}</dd>`);
  if (w.since_age_s != null) rows.push(`<dt>Started</dt><dd>${ago(w.since_age_s)}</dd>`);
  if (!w.busy && w.last_result) rows.push(`<dt>Last build</dt><dd>${badge(w.last_result === "FAIL" ? "FAILED" : "ATTEMPTED")}${esc(w.last_summary || "")}</dd>`);
  $("now").innerHTML = `<div class="big">${esc(w.text || "")}</div>${rows.length ? `<dl class="kv" style="margin-top:8px">${rows.join("")}</dl>` : ""}`;
}

function renderModel(m) {
  const state = {GENERATING: "Generating", BETWEEN_CALLS: "Between model calls", LOADED_IDLE: "Loaded, idle", UNLOADED: "Unloaded", UNKNOWN: "Unknown"}[m?.state] || "Unknown";
  const loaded = (m?.loaded || []).map((x) => `${esc(x.name)} · ${esc(x.size_gib)} GiB · ${esc(x.processor)}`).join("<br>");
  const a = m?.last_admission;
  $("model").innerHTML = `<div class="big">${esc(state)}</div><div class="sub">${esc(m?.text || "")}</div>` +
    (loaded ? `<div style="margin-top:8px">${loaded}</div>` : "") +
    (a ? `<div class="sub" style="margin-top:8px">Last memory decision ${clock(a.at_utc)}: ${esc(a.kind)} ${esc(a.model || "")} (${esc(a.available_gib)} GiB free, needs ${esc(a.required_gib)})</div>` : "");
}

function renderEngine(e) {
  $("engine").innerHTML = `<dl class="kv">
    <dt>Mode</dt><dd>${esc(e?.mode_text || "unknown")}</dd>
    <dt>Supervisor</dt><dd>${e?.supervisor_alive === true ? "running" : e?.supervisor_alive === false ? '<span class="stale">not running</span>' : "unknown"}${e?.engine_processes != null ? " (" + esc(e.engine_processes) + " processes)" : ""}</dd>
    <dt>Last burst</dt><dd>${ago(e?.heartbeat_age_s)}</dd>
    <dt>Release</dt><dd>${unknown(e?.release)}</dd></dl>`;
}

function renderResources(r) {
  const used = r?.ram_total_gib != null && r?.ram_free_gib != null ? r.ram_total_gib - r.ram_free_gib : null;
  const disk = r?.disk_total_gib != null && r?.disk_free_gib != null ? r.disk_total_gib - r.disk_free_gib : null;
  const dev = r?.dev_mode?.active ? `Dev Mode ON (closed: ${esc((r.dev_mode.closed || []).join(", ") || "nothing")})` : "Dev Mode off";
  $("resources").innerHTML = `<div>RAM ${unknown(r?.ram_free_gib, " GiB free")} of ${unknown(r?.ram_total_gib, " GiB")}</div>${bar(used, r?.ram_total_gib)}
    <div>CPU ${unknown(r?.cpu_percent, "%")}</div>${bar(r?.cpu_percent, 100)}
    <div>Disk ${unknown(r?.disk_free_gib, " GiB free")}</div>${bar(disk, r?.disk_total_gib)}
    <div class="sub">${dev}</div>`;
}

function renderProgress(p) {
  if (!p) { $("progress").innerHTML = unknown(null); return; }
  const delivered = (p.verified_deliveries || []).slice(0, 6).map((d) =>
    `<li>${badge("VERIFIED")}<b>${repo(d.repository)} ${d.pr_url ? `<a href="${esc(d.pr_url)}" target="_blank" rel="noopener">#${esc(d.pr)}</a>` : "#" + esc(d.pr)}</b> ${esc(d.title)}<div class="sub">merged ${clock(d.merged_at_utc)} ${esc(d.merged_at_utc ? new Date(d.merged_at_utc).toLocaleDateString() : "")} · ${esc(d.merged_sha || "")}</div></li>`).join("") || '<li class="sub">No merged pull request recorded.</li>';
  const plans = (p.plans || []).slice(0, 5).map((x) =>
    `<li>${badge(x.status === "BLOCKED" ? "FAILED" : x.evidence)}<b>${repo(x.repository)}</b> ${esc(x.parent)}<div class="sub">${esc(x.status)} · ${x.steps.length} step(s)${x.steps.length ? ": " + esc(x.steps.join(", ")) : ""}</div>` +
    ((x.outstanding_criteria || []).length ? `<div class="sub">Outstanding criteria: ${esc(x.outstanding_criteria.join(" · "))}</div>` : "") +
    (x.blocker ? `<div class="sub">${esc(x.blocker)}</div>` : "") + "</li>").join("") || '<li class="sub">No plans.</li>';
  const fails = (p.recent_failures || []).slice(0, 6).map((f) =>
    `<li>${badge("FAILED")}${esc(f.task)}<div class="sub">${esc(f.outcome)} · ${esc(f.attempts)} attempt(s) · ${clock(f.updated_at_utc)}</div></li>`).join("") || '<li class="sub">No failed tasks in the window.</li>';
  const open = (p.open_pull_requests || []).map((o) => `<li>${badge("ATTEMPTED")}${repo(o.repository)} ${o.pr_url ? `<a href="${esc(o.pr_url)}" target="_blank" rel="noopener">#${esc(o.pr)}</a>` : "#" + esc(o.pr)} ${esc(o.title)} <span class="sub">${esc(o.state)}</span></li>`).join("");
  $("progress").innerHTML = `<div class="stat">
      <div><b>${esc(p.verified_in_window)}</b><span class="sub">merged in ${esc(p.window_hours)} h</span></div>
      <div><b>${esc(p.build_attempts_in_window)}</b><span class="sub">build attempts</span></div>
      <div><b>${esc(p.failed_tasks_in_window)}</b><span class="sub">tasks failed</span></div>
      <div><b>${esc((p.open_pull_requests || []).length)}</b><span class="sub">open PRs</span></div></div>
    <div class="cols"><div><div class="sub">Verified deliveries</div><ul class="list">${delivered}${open}</ul></div>
    <div><div class="sub">Plans</div><ul class="list">${plans}</ul></div>
    <div><div class="sub">Recent failures</div><ul class="list">${fails}</ul></div></div>`;
}

function renderWaiting(w) {
  const reasons = Object.entries(w?.by_reason || {}).sort((a, b) => b[1] - a[1]);
  const byCode = {};
  for (const item of w?.items || []) (byCode[item.code] ||= []).push(item);
  const rows = reasons.map(([code, n]) => {
    const items = byCode[code] || [];
    const why = items[0]?.why || code;
    const list = items.slice(0, 5).map((i) => `${repo(i.repository)}: ${esc(i.slice)}`).join(" · ");
    return `<li><b>${esc(n)}</b> ${esc(why)}<div class="sub">${esc(code)}${list ? " — " + list : ""}${items.length > 5 ? " …" : ""}</div></li>`;
  }).join("");
  $("waiting").innerHTML = rows ? `<ul class="list">${rows}</ul>` : '<div class="sub">Nothing is waiting.</div>';
}

function renderProjects(projects) {
  const rows = (projects || []).map((p) => `<tr><td><b>${esc(p.repository)}</b></td><td>${esc(p.slices)}</td><td>${esc(p.verified_merged_prs)}</td>
    <td>${p.next_eligible ? esc(p.next_eligible.slice) + ` <span class="sub">${esc(p.next_eligible.stage || "")}</span>` : '<span class="sub">none ready</span>'}</td>
    <td class="hide-sm">${esc(p.top_blocker || "")}</td></tr>`).join("");
  $("projects").innerHTML = rows ? `<table><thead><tr><th>Project</th><th>Slices</th><th>Merged</th><th>Next eligible</th><th class="hide-sm">Top blocker</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="sub">No projects in the trace.</div>';
}

function renderActivity(feed) {
  $("activity").innerHTML = (feed || []).map((a) => `<li class="${esc(a.class)}"><time>${clock(a.at_utc)}</time>
    <div><span class="dot"></span>${esc(a.text)}${a.repository ? ` <span class="sub">· ${esc(a.repository)}</span>` : ""}${a.item ? `<div class="sub">${esc(a.item)}</div>` : ""}${a.detail ? `<div class="sub">${esc(a.detail)}</div>` : ""}</div></li>`).join("") || '<li class="sub">No activity recorded.</li>';
}

async function refresh() {
  try {
    const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 8000);
    const response = await fetch("/api/command-center", {cache: "no-store", signal: ctl.signal});
    clearTimeout(timer);
    if (!response.ok) throw new Error("HTTP " + response.status);
    const s = await response.json();
    renderVerdict(s.verdict, s.engine); renderNow(s.work); renderModel(s.model); renderEngine(s.engine);
    renderResources(s.resources); renderProgress(s.progress); renderWaiting(s.waiting); renderProjects(s.projects); renderActivity(s.activity);
    lastOk = Date.now();
    $("age").textContent = "updated " + new Date(s.observed_at_utc).toLocaleTimeString() + (s.errors ? " · partial: " + s.errors.join(", ") : "");
    $("age").className = "muted";
  } catch (error) {
    const stale = lastOk ? Math.round((Date.now() - lastOk) / 1000) + " s old" : "never loaded";
    $("age").textContent = "not updating (" + error.message + "); data " + stale;
    $("age").className = "muted stale";
  }
}
refresh();
setInterval(refresh, REFRESH_MS);
