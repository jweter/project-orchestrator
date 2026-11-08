"""Dev Mode: a bare-bones, reversible machine state so the engine can develop when memory is short.

The owner asked for this on 2026-11-08 ("I'm cool with going completely bare bones"). It frees memory by closing a *named*
list of known memory hogs (browsers, cloud-sync clients, chat and media apps, Windows widget extras) and leaves everything
else running. Four rules keep it safe:

1. **NEVER list.** Windows core, security software, drivers and input, remote access (Tailscale), Ollama, the engine and every
   developer tool (Claude Code, Python, Git, shells, Node, Unreal/MSBuild) are never touched, whatever the config says.
2. **Allowlist, not blocklist.** Only names on the close list are closed. A program nobody classified is reported as
   UNCLASSIFIED and left running; the owner decides whether to add it (``dev-mode.json`` ``close_extra``).
3. **Politeness.** An app with a window gets a normal close request (the app may ask to save and the request is then
   respected: it is reported as REFUSED and never forced). Only window-less helper processes are force-ended after a grace
   period, because they hold no document state.
4. **Reversible.** Every closed app's launch command is recorded; ``restore`` relaunches what was closed and is safe to run
   twice. Entering needs an explicit confirmation, and nothing in the engine enters Dev Mode by itself.

All system access goes through injected callables so the policy is tested without touching a real process.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1
STATE_NAME = "dev-mode-state.json"
CONFIG_NAME = "dev-mode.json"
GRACE_SECONDS = 15.0

# --- rule 1: never touched, by name pattern (case-insensitive, whole-name regex) -------------------------------------------
NEVER_NAMES = (
    r"system|idle|registry|memory compression|smss|csrss|wininit|winlogon|services|lsass|lsaiso|dwm|explorer|fontdrvhost|"
    r"sihost|ctfmon|taskhostw?|svchost|conhost|dllhost|runtimebroker|searchhost|searchindexer|startmenuexperiencehost|"
    r"textinputhost|shellhost|shellexperiencehost|applicationframehost|securityhealth.*|msmpeng|nissrv|"
    r"norton.*|nav.*|ns|nortonui|model_host|integrator|microsoftsecurityapp|avast.*|avg.*|"
    r"tailscale.*|ollama.*|llama-server|"
    r"claude|node|npm|npx|python.*|pythonw.*|uv|uvx|aider.*|git|git-.*|bash|sh|cmd|powershell.*|pwsh|windowsterminal|wt|"
    r"msbuild|dotnet|cmake|ctest|cl|link|ninja|unrealeditor.*|unrealbuildtool.*|shadercompileworker|ubacoordinator.*|"
    r"rtk.*|bridgecommunication|igfx.*|intel.*|nvidia.*|nvcontainer|amd.*|hp.*|audiodg"
)
NEVER_RE = re.compile(rf"^(?:{NEVER_NAMES})$", re.I)
# ... and by location: Windows itself, drivers, the engine's own files
NEVER_PATH_PARTS = ("\\windows\\system32\\", "\\windows\\systemapps\\", "\\windows\\syswow64\\", "\\driverstore\\",
                    "\\exponentialengine\\", "\\.ollama\\", "\\programs\\ollama\\", "\\program files\\norton\\", "\\tailscale\\",
                    "\\epic games\\ue_", "\\.local\\bin\\", "\\program files\\git\\", "\\program files\\nodejs\\")

# --- rule 2: the close list. Programs that may hold unsaved work are GUARDED: a polite request, never forced. ------------------
CLOSE_NAMES = (
    r"chrome|msedge|firefox|brave|opera|vivaldi|msedgewebview2|"
    r"chatgpt|discord|slack|teams|ms-teams|spotify|zoom|steam|steamwebhelper|epicgameslauncher|whatsapp|telegram|"
    r"onedrive|filecoauth|googledrivefs|dropbox|"
    r"widgetboard|widgetservice|crossdeviceservice|crossdeviceresume|bingwebsearch|storedesktopextension|appactions|"
    r"phoneexperiencehost|gamebar.*|xboxpcapp|acrotray|adobecollabsync|adobearm|adobeipcbroker|ccxprocess|creativecloud.*"
)
CLOSE_RE = re.compile(rf"^(?:{CLOSE_NAMES})$", re.I)
GUARDED_NAMES = r"chrome|msedge|firefox|brave|opera|vivaldi|chatgpt|discord|slack|teams|ms-teams|zoom|whatsapp|telegram|xboxpcapp"
GUARDED_RE = re.compile(rf"^(?:{GUARDED_NAMES})$", re.I)
RESTORE_FLAGS = {"chrome": ["--restore-last-session"], "msedge": ["--restore-last-session"]}

CLOSE, KEEP, UNCLASSIFIED = "CLOSE", "KEEP", "UNCLASSIFIED"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Group:
    """All processes of one program in the owner's session, with the evidence the decision rests on."""
    name: str
    pids: list[int]
    mib: int
    windows: int
    path: str
    root_pid: int | None = None
    root_command: str = ""
    action: str = UNCLASSIFIED
    reason: str = ""
    guarded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "count": len(self.pids), "mib": self.mib, "windows": self.windows, "path": self.path,
                "action": self.action, "reason": self.reason, "guarded": self.guarded}


def _norm(path: str) -> str:
    return str(path or "").lower().replace("/", "\\")


def load_config(state_root: Path | None) -> dict[str, list[str]]:
    """Optional owner edits. ``close_extra`` adds programs to the close list; nothing here can override the NEVER rules."""
    try:
        data = json.loads((Path(state_root) / CONFIG_NAME).read_text(encoding="utf-8")) if state_root else {}
    except (OSError, ValueError):
        data = {}
    clean = lambda key: [str(x).strip().lower() for x in data.get(key, []) if isinstance(x, str) and re.fullmatch(r"[\w.\- ]{1,60}", x.strip())]
    return {"close_extra": clean("close_extra"), "keep_extra": clean("keep_extra")}


def classify(name: str, path: str, *, config: dict[str, list[str]] | None = None, protected_pids: bool = False) -> tuple[str, str, bool]:
    """(action, reason, guarded) for one program. NEVER rules win over every other input, including the config."""
    config = config or {"close_extra": [], "keep_extra": []}
    lowered = name.lower()
    where = _norm(path)
    if protected_pids:
        return KEEP, "part of the process tree running this command", False
    if NEVER_RE.match(lowered):
        return KEEP, "protected: Windows, security, drivers, remote access, the engine or a developer tool", False
    if any(part in where for part in NEVER_PATH_PARTS):
        return KEEP, "protected location: Windows, drivers or the engine's own files", False
    if lowered in config["keep_extra"]:
        return KEEP, "kept by the owner's dev-mode.json", False
    if CLOSE_RE.match(lowered) or lowered in config["close_extra"]:
        return CLOSE, "on the Dev Mode close list", bool(GUARDED_RE.match(lowered)) or lowered in config["close_extra"]
    return UNCLASSIFIED, "not on any list: left running (add it to close_extra in dev-mode.json to close it)", False


def snapshot_processes() -> list[dict[str, Any]]:
    """The owner's session processes: pid, parent, name, path, command line, working set, whether it has a window."""
    script = (
        "$sid=(Get-Process -Id $PID).SessionId; $win=@{}; Get-Process | ? { $_.SessionId -eq $sid -and $_.MainWindowHandle -ne 0 } | "
        "% { $win[[int]$_.Id]=1 }; Get-CimInstance Win32_Process | ? { $_.SessionId -eq $sid } | % { [pscustomobject]@{"
        "pid=[int]$_.ProcessId;ppid=[int]$_.ParentProcessId;name=($_.Name -replace '\\.exe$','');path=$_.ExecutablePath;"
        "cmd=$_.CommandLine;mib=[int]($_.WorkingSetSize/1MB);window=[bool]$win.ContainsKey([int]$_.ProcessId)} } | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60, check=True).stdout
    data = json.loads(out or "[]")
    return data if isinstance(data, list) else [data]


def _ancestors(rows: list[dict[str, Any]], pid: int) -> set[int]:
    by = {int(r["pid"]): int(r.get("ppid") or 0) for r in rows}
    chain, current = set(), pid
    while current in by and current not in chain:
        chain.add(current)
        current = by[current]
    return chain


def plan(rows: list[dict[str, Any]] | None = None, *, config: dict[str, list[str]] | None = None, self_pid: int | None = None) -> list[Group]:
    """Group the session's processes by program and decide, per program, close or keep. Pure: touches nothing."""
    rows = snapshot_processes() if rows is None else rows
    mine = _ancestors(rows, os.getpid() if self_pid is None else self_pid)
    grouped: dict[str, Group] = {}
    for row in rows:
        name = str(row.get("name") or "")
        group = grouped.setdefault(name.lower(), Group(name=name, pids=[], mib=0, windows=0, path=str(row.get("path") or "")))
        group.pids.append(int(row["pid"]))
        group.mib += int(row.get("mib") or 0)
        group.windows += 1 if row.get("window") else 0
        group.path = group.path or str(row.get("path") or "")
    same = {key: set(g.pids) for key, g in grouped.items()}
    for key, group in grouped.items():
        tree = any(int(r["pid"]) in mine and str(r.get("name") or "").lower() == key for r in rows)
        group.action, group.reason, group.guarded = classify(group.name, group.path, config=config, protected_pids=tree)
        roots = [r for r in rows if str(r.get("name") or "").lower() == key and int(r.get("ppid") or 0) not in same[key]]
        if roots:  # the program's own top process: the one worth relaunching on restore
            root = sorted(roots, key=lambda r: (not r.get("window"), -int(r.get("mib") or 0)))[0]
            group.root_pid, group.root_command = int(root["pid"]), str(root.get("cmd") or "")
    return sorted(grouped.values(), key=lambda g: g.mib, reverse=True)


def summary(groups: list[Group]) -> dict[str, Any]:
    closable = [g for g in groups if g.action == CLOSE]
    return {"reclaimable_mib": sum(g.mib for g in closable), "close": [g.as_dict() for g in closable],
            "keep_top": [g.as_dict() for g in groups if g.action == KEEP][:8],
            "unclassified": [g.as_dict() for g in groups if g.action == UNCLASSIFIED and g.mib >= 40][:12]}


# --- entering and leaving -------------------------------------------------------------------------------------------------
def system_closer(pid: int, *, force: bool) -> bool:
    """Ask a process to close. Without ``force`` this is ``taskkill /PID`` (WM_CLOSE to every window of the process, so a
    browser with several windows closes them all and an app with a save prompt can still ask). Window-less helpers refuse
    the polite form and only end when forced."""
    command = ["taskkill.exe", "/PID", str(pid)] + (["/F"] if force else [])
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False).returncode == 0


def alive_pids(pids: list[int]) -> set[int]:
    if not pids:
        return set()
    script = "Get-Process -Id " + ",".join(str(p) for p in pids) + " -ErrorAction SilentlyContinue | % { $_.Id }"
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=30, check=False).stdout
    return {int(x) for x in out.split() if x.strip().isdigit()}


def enter(groups: list[Group], state_path: Path, *, confirmed: bool, closer: Callable[..., bool] = system_closer,
          alive: Callable[[list[int]], set[int]] = alive_pids, free_gib: Callable[[], float] | None = None,
          sleep: Callable[[float], None] = time.sleep, grace: float = GRACE_SECONDS, entered_by: str = "owner",
          reason: str = "") -> dict[str, Any]:
    """Close the planned groups politely, record what was closed, and report what is still running and why."""
    if not confirmed:
        raise PermissionError("Dev Mode closes programs: pass confirmed=True (the CLI needs --yes).")
    if state_path.is_file() and json.loads(state_path.read_text(encoding="utf-8")).get("active"):
        raise RuntimeError("Dev Mode is already active; run restore first.")
    before = free_gib() if free_gib else None
    closed, refused, forced = [], [], []
    for group in [g for g in groups if g.action == CLOSE]:
        record = {"name": group.name, "path": group.path, "command": group.root_command, "mib": group.mib,
                  "windows": group.windows, "closed_at": _now()}
        pids = list(group.pids)
        for pid in pids:                                              # polite request: windows only (the app may ask to save)
            closer(pid, force=False)
        deadline = time.monotonic() + grace
        while alive(pids) and time.monotonic() < deadline:
            sleep(1.0)
        left = alive(pids)
        for pid in sorted(left):
            if group.guarded and group.windows:                       # may hold unsaved work: never forced
                continue
            if closer(pid, force=True):                               # window-less helpers hold no document state
                forced.append(pid)
        still = alive(pids)
        if still and group.guarded and group.windows:
            refused.append({"name": group.name, "pids": sorted(still), "why": "the app kept running after a polite close request (it may be asking to save); not forced"})
        if len(still) < len(pids):
            record["closed_pids"] = len(pids) - len(still)
            closed.append(record)
    sleep(2.0)
    after = free_gib() if free_gib else None
    state = {"schema_version": SCHEMA, "active": True, "entered_at_utc": _now(), "entered_by": entered_by, "reason": reason,
             "closed": closed, "refused": refused,
             "forced_helper_pids": forced, "free_gib_before": before, "free_gib_after": after}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, state_path)
    return state


def launcher(path: str, args: list[str]) -> bool:
    quoted = ", ".join("'" + a.replace("'", "''") + "'" for a in args)
    command = f"Start-Process -FilePath '{path}'" + (f" -ArgumentList {quoted}" if args else "")
    return subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=30,
                          check=False).returncode == 0


def restore(state_path: Path, *, running: Callable[[], set[str]] | None = None,
            start: Callable[[str, list[str]], bool] = launcher, config: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Relaunch what Dev Mode closed (main program only, browsers with their own session restore). Safe to run twice."""
    if not state_path.is_file():
        return {"restored": [], "skipped": [], "note": "Dev Mode is not active"}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not state.get("active"):
        return {"restored": [], "skipped": [], "note": "Dev Mode is not active"}
    up = {n.lower() for n in (running() if running else {r["name"] for r in snapshot_processes()})}
    restored, skipped = [], []
    for record in state.get("closed", []):
        name, path = str(record["name"]), str(record.get("path") or "")
        if classify(name, path, config=config)[0] != CLOSE or name.lower() in up:
            skipped.append({"name": name, "why": "already running or no longer a Dev Mode program"})
            continue
        if not path or not Path(path).is_file():
            skipped.append({"name": name, "why": "its program file was not found"})
            continue
        ok = start(path, RESTORE_FLAGS.get(name.lower(), []))
        (restored if ok else skipped).append({"name": name} if ok else {"name": name, "why": "relaunch failed"})
    state.update(active=False, restored_at_utc=_now(), restored=[r["name"] for r in restored])
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, state_path)
    return {"restored": restored, "skipped": skipped, "note": "Dev Mode ended"}


def status(state_path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(state_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {"active": False}


# --- the standing rule: enter automatically when development is stalled by memory ------------------------------------------
# Owner decision 2026-11-08: "if development stalls because of RAM then enter into dev mode and try to continue". The rule is
# deliberately narrow so it cannot misfire: it needs Take the Wheel to be active (the lane only gets here then), authorised
# work blocked by memory alone for ``stall_minutes`` in a row, and proof that the approved close list would actually make the
# blocked model fit. It never closes anything when that would not help, and it is rate limited.
AUTO_NAME = "dev-mode-auto.json"
AUTO_DEFAULTS = {"auto": False, "stall_minutes": 3.0, "exit_idle_minutes": 15.0, "cooldown_minutes": 30.0,
                 "max_per_day": 2, "margin_gib": 0.3}


def auto_config(raw: Any) -> dict[str, Any]:
    """The policy's ``dev_mode`` section with safe defaults: absent or malformed means the rule is OFF."""
    if not isinstance(raw, dict) or raw.get("auto") is not True:
        return dict(AUTO_DEFAULTS)
    merged = dict(AUTO_DEFAULTS)
    merged["auto"] = True
    for key in ("stall_minutes", "exit_idle_minutes", "cooldown_minutes", "margin_gib"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            merged[key] = float(value)
    limit = raw.get("max_per_day")
    if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0:
        merged["max_per_day"] = limit
    return merged


@dataclass(frozen=True)
class AutoDecision:
    action: str  # OFF, ALREADY_ACTIVE, WAIT, LIMIT, COOLDOWN, NO_HELP, ENTER
    reason: str


def auto_decide(config: dict[str, Any], *, now: float, stalled_since: float | None, active: bool, last_exit: float | None,
                entries_today: int, reclaimable_gib: float, free_gib: float | None, required_gib: float | None) -> AutoDecision:
    if not config["auto"]:
        return AutoDecision("OFF", "the standing rule is not enabled in the policy")
    if active:
        return AutoDecision("ALREADY_ACTIVE", "Dev Mode is already on")
    if stalled_since is None:
        return AutoDecision("WAIT", "memory is not what is blocking development")
    stalled_for = (now - stalled_since) / 60.0
    if stalled_for < config["stall_minutes"]:
        return AutoDecision("WAIT", f"stalled on memory for {stalled_for:.0f} of {config['stall_minutes']:.0f} minutes")
    if entries_today >= config["max_per_day"]:
        return AutoDecision("LIMIT", f"already entered {entries_today} time(s) today (limit {config['max_per_day']})")
    if last_exit is not None and (now - last_exit) / 60.0 < config["cooldown_minutes"]:
        return AutoDecision("COOLDOWN", f"left Dev Mode {(now - last_exit) / 60.0:.0f} min ago (cooldown {config['cooldown_minutes']:.0f})")
    if free_gib is None or required_gib is None:
        return AutoDecision("WAIT", "free or required memory is unknown, so closing programs cannot be shown to help")
    potential = free_gib + reclaimable_gib
    if potential < required_gib + config["margin_gib"]:
        return AutoDecision("NO_HELP", (f"closing everything on the approved list would free about {reclaimable_gib:.1f} GiB, giving "
                                         f"{potential:.1f} GiB; {required_gib + config['margin_gib']:.1f} GiB is needed. Nothing was closed."))
    return AutoDecision("ENTER", f"stalled {stalled_for:.0f} min; closing the list gives about {potential:.1f} GiB of the {required_gib:.1f} GiB needed")


def _read_auto(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def engine_step(state_root: Path, raw_config: Any, *, waiting_for_memory: bool, idle: bool, required_gib: float | None,
                free_gib: float | None, now: float | None = None, plan_fn: Callable[[], list[Group]] | None = None,
                enter_fn: Callable[..., dict[str, Any]] | None = None,
                restore_fn: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """One tick of the standing rule, called with what the lane just observed. Returns what it did (or None: rule is off).

    ``waiting_for_memory`` means authorised work exists and memory is the only thing stopping it; ``idle`` means there is no
    work to build. Both false means the engine is doing something else, which resets both clocks.
    """
    config = auto_config(raw_config)
    if not config["auto"]:
        return None
    now = time.time() if now is None else now
    state_root = Path(state_root)
    auto_path, state_path = state_root / AUTO_NAME, state_root / STATE_NAME
    auto = _read_auto(auto_path)
    active = bool(status(state_path).get("active"))
    out: dict[str, Any] | None = None

    auto["stalled_since"] = (auto.get("stalled_since") or now) if waiting_for_memory else None
    auto["idle_since"] = (auto.get("idle_since") or now) if idle else None

    if active and auto["idle_since"] is not None and (now - auto["idle_since"]) / 60.0 >= config["exit_idle_minutes"]:
        result = (restore_fn or restore)(state_path, config=load_config(state_root))
        auto["last_exit"] = now
        auto["idle_since"] = None
        out = {"action": "RESTORED", "reason": f"nothing to build for {config['exit_idle_minutes']:.0f} minutes",
               "restored": [r.get("name") for r in result.get("restored", [])]}
    elif waiting_for_memory and not active:
        entries_today = sum(1 for t in auto.get("entries", []) if now - float(t) < 86400)
        groups = (plan_fn or (lambda: plan(config=load_config(state_root))))()
        reclaim = summary(groups)["reclaimable_mib"] / 1024.0
        decision = auto_decide(config, now=now, stalled_since=auto["stalled_since"], active=active, last_exit=auto.get("last_exit"),
                               entries_today=entries_today, reclaimable_gib=reclaim, free_gib=free_gib, required_gib=required_gib)
        out = {"action": decision.action, "reason": decision.reason}
        if decision.action == "ENTER":
            state = (enter_fn or enter)(groups, state_path, confirmed=True,
                                        free_gib=(lambda: free_gib) if free_gib is not None else None,
                                        entered_by="engine-auto", reason=decision.reason)
            auto["entries"] = [t for t in auto.get("entries", []) if now - float(t) < 86400] + [now]
            auto["stalled_since"] = None
            out.update(closed=[c["name"] for c in state.get("closed", [])], refused=[r["name"] for r in state.get("refused", [])])
    auto["last_decision"] = {"at_utc": _now(), **(out or {"action": "NONE", "reason": "nothing for the rule to do"})}
    _write_atomic(auto_path, auto)
    return out
