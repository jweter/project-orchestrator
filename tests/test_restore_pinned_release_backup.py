"""Fail-closed tests for restoring the records saved by a pinned-release promotion (rollback readiness)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.owner_authority import grant_owner_authority, revoke_owner_authority

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "desktop-control" / "Restore-PinnedReleaseBackup.ps1"
PS = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(PS is None, reason="Windows PowerShell required")
NEW_SHA = "b" * 40
WATCHDOG = "ExponentialEngine-ContinuousFlow-Watchdog"
ACTOR = "ExponentialEngine-LaptopActor"
DESKTOP = ["DesktopControlSilent.vbs", "Take-The-Wheel.ps1"]
TASK_XML = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Settings><Enabled>{enabled}</Enabled></Settings>'
    "<Actions><Exec><Command>powershell.exe</Command><Arguments>-File {script} {sha}</Arguments></Exec></Actions></Task>"
)


def git(path: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "core.longpaths=true", "-C", str(path), *args], capture_output=True, text=True, check=True, timeout=60,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"},
    )
    return done.stdout.strip()


def tree_hash(path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.rglob("*")) if p.is_file()
    }


def without(snapshot: dict[str, str], *prefixes: str) -> dict[str, str]:
    return {k: v for k, v in snapshot.items() if not k.startswith(prefixes)}


@pytest.fixture
def install(tmp_path: Path):
    """A sandbox engine root that has just been promoted from OLD to NEW, with the promotion's backup on disk."""
    base = tmp_path / "engine"
    state = base / "flow-state"
    state.mkdir(parents=True)
    release_src = base / "_seed"
    (release_src / "scripts" / "desktop-control").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "desktop-control" / "IronGodOwnerControl.ps1",
                release_src / "scripts" / "desktop-control")
    shutil.copytree(ROOT / "src", release_src / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    git(release_src, "init", "-q")
    git(release_src, "config", "core.longpaths", "true")
    git(release_src, "add", "-A")
    git(release_src, "commit", "-qm", "old release")
    old = git(release_src, "rev-parse", "HEAD")
    release = base / "releases" / old
    release.parent.mkdir()
    shutil.move(str(release_src), str(release))
    actor = base / "runtime-actors" / old
    actor.parent.mkdir()
    subprocess.run(["git", "-c", "core.longpaths=true", "clone", "-q", str(release), str(actor)], check=True, capture_output=True, timeout=120)
    git(actor, "config", "core.longpaths", "true")
    tree = git(release, "rev-parse", "HEAD^{tree}")
    (base / "release-receipts").mkdir()
    (base / "release-receipts" / f"{old}.json").write_text(
        json.dumps({"accepted_sha": old, "tree": tree, "status": "VERIFIED_STAGED"}), encoding="utf-8")

    old_pin = {"schema_version": 1, "enabled": True, "accepted_repo": str(release), "accepted_sha": old}
    old_actor_pin = {"enabled": True, "accepted_repo": str(release), "accepted_sha": old, "actor_repo": str(actor)}
    new_pin = {**old_pin, "accepted_repo": str(base / "releases" / NEW_SHA), "accepted_sha": NEW_SHA}
    new_actor_pin = {**old_actor_pin, "accepted_repo": str(base / "releases" / NEW_SHA), "accepted_sha": NEW_SHA}

    backup = state / "pinned-release-promotion-0123456789abcdef"
    (backup / "desktop-controls").mkdir(parents=True)
    (backup / "supervisor-pinned-release.before.json").write_text(json.dumps(old_pin), encoding="utf-8")
    (backup / "actor-local-promotion.before.json").write_text(json.dumps(old_actor_pin), encoding="utf-8")
    (backup / "promotion-result.json").write_text(json.dumps({"old_sha": old, "accepted_sha": NEW_SHA}), encoding="utf-8")
    sandbox = base / "task-sandbox"
    sandbox.mkdir()
    for name, script in ((WATCHDOG, "watch_pinned_continuous_flow.ps1"), (ACTOR, "launch_local_actor.ps1")):
        (backup / f"{name}.xml").write_text(TASK_XML.format(enabled="false", script=script, sha=old), encoding="utf-8")
        (sandbox / f"{name}.xml").write_text(TASK_XML.format(enabled="false", script=script, sha=NEW_SHA), encoding="utf-8")
        (sandbox / f"{name}.state").write_text("Disabled", encoding="utf-8")
    for name in DESKTOP:
        (backup / "desktop-controls" / name).write_text(f"old {name}\n", encoding="utf-8")
        (base / name).write_text(f"new {name}\n", encoding="utf-8")

    (state / "supervisor-pinned-release.json").write_text(json.dumps(new_pin), encoding="utf-8")
    (state / "actor-local-promotion.json").write_text(json.dumps(new_actor_pin), encoding="utf-8")
    revoke_owner_authority(state)  # the owner holds control
    return {"base": base, "state": state, "backup": backup, "old": old, "release": release}


def run(inst, *extra: str, apply: bool = False, sha: str | None = None):
    argv = [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
            "-ExpectedSha", sha or inst["old"], "-Base", str(inst["base"]), "-TaskBackend", "Files",
            "-BackupDir", str(inst["backup"]), *extra]
    if apply:
        argv.append("-Apply")
    return subprocess.run(argv, capture_output=True, text=True, timeout=180,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def read(inst, rel: str) -> str:
    return (inst["base"] / rel).read_text(encoding="utf-8")


def restored(inst) -> bool:
    pins = [json.loads(read(inst, f"flow-state/{n}"))["accepted_sha"]
            for n in ("supervisor-pinned-release.json", "actor-local-promotion.json")]
    return pins == [inst["old"], inst["old"]]


def test_dry_run_validates_and_changes_nothing(install):
    before = tree_hash(install["base"])
    proc = run(install)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    plan = json.loads(proc.stdout)
    assert plan["mode"] == "DRY_RUN" and plan["mutations_performed"] == 0
    assert plan["restore_sha"] == install["old"] and plan["replaces_sha"] == NEW_SHA
    assert any(item == "task:" + WATCHDOG for item in plan["restores"])
    # The owner controller keeps its own state under IronGodControl; everything else must be byte-identical.
    assert without(tree_hash(install["base"]), "flow-state\\IronGodControl") == without(before, "flow-state\\IronGodControl")
    assert not list(install["state"].glob("pre-restore-*"))


def test_apply_restores_pins_tasks_and_desktop_controls_and_keeps_everything_else(install):
    backup_before = tree_hash(install["backup"])
    control_before = tree_hash(install["state"] / "IronGodControl")
    proc = run(install, apply=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    result = json.loads(proc.stdout)
    assert result["status"] == "PINNED_RELEASE_RESTORED_TASKS_DISABLED" and result["tasks_enabled"] is False
    assert restored(install)
    for name in (WATCHDOG, ACTOR):
        assert install["old"] in read(install, f"task-sandbox/{name}.xml")
        assert read(install, f"task-sandbox/{name}.state") == "Disabled"
    assert all(read(install, n) == f"old {n}\n" for n in DESKTOP)
    assert tree_hash(install["backup"]) == backup_before  # the backup is never altered
    assert tree_hash(install["state"] / "IronGodControl") == control_before  # owner control untouched
    snapshot = next(install["state"].glob("pre-restore-*"))
    assert (snapshot / "restore-result.json").is_file() and not (snapshot / "restore-failed.json").exists()


def test_a_second_apply_is_a_safe_no_op_repeat(install):
    assert run(install, apply=True).returncode == 0
    again = run(install, apply=True)
    assert again.returncode == 0, again.stderr + again.stdout
    assert restored(install)


@pytest.mark.parametrize("missing", [
    "supervisor-pinned-release.before.json", "actor-local-promotion.before.json", "promotion-result.json",
    f"{WATCHDOG}.xml", f"{ACTOR}.xml", "desktop-controls",
])
def test_an_incomplete_backup_is_refused_before_any_write(install, missing):
    target = install["backup"] / missing
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    before = without(tree_hash(install["base"]), "flow-state\\IronGodControl")
    proc = run(install, apply=True)
    assert proc.returncode != 0 and "BACKUP" in (proc.stderr + proc.stdout)
    assert without(tree_hash(install["base"]), "flow-state\\IronGodControl") == before
    assert not list(install["state"].glob("pre-restore-*"))


@pytest.mark.parametrize("name,content,code", [
    ("supervisor-pinned-release.before.json", "{not json", "BACKUP_PIN_CORRUPT"),
    ("actor-local-promotion.before.json", "", "BACKUP_ACTOR_PIN_CORRUPT"),
    ("promotion-result.json", "[", "BACKUP_PROMOTION_RESULT_CORRUPT"),
    (f"{WATCHDOG}.xml", "<Task><unclosed>", "BACKUP_TASK_XML_CORRUPT"),
    (f"{ACTOR}.xml", TASK_XML.format(enabled="true", script="launch_local_actor.ps1", sha="X"), "BACKUP_TASK"),
])
def test_a_corrupt_backup_is_refused_before_any_write(install, name, content, code):
    (install["backup"] / name).write_text(content.replace("X", install["old"]), encoding="utf-8")
    before = without(tree_hash(install["base"]), "flow-state\\IronGodControl")
    proc = run(install, apply=True)
    assert proc.returncode != 0 and code in (proc.stderr + proc.stdout)
    assert without(tree_hash(install["base"]), "flow-state\\IronGodControl") == before


def test_a_backup_that_would_re_enable_a_task_is_never_a_restore_source(install):
    xml = TASK_XML.format(enabled="true", script="launch_local_actor.ps1", sha=install["old"])
    (install["backup"] / f"{ACTOR}.xml").write_text(xml, encoding="utf-8")
    proc = run(install, apply=True)
    assert proc.returncode != 0 and "BACKUP_TASK_NOT_DISABLED" in (proc.stderr + proc.stdout)
    assert not restored(install)


@pytest.mark.parametrize("mutate,code", [
    ("expected", "BACKUP_OLD_SHA_MISMATCH"),
    ("before_pin", "BACKUP_PIN_SHA_MISMATCH"),
    ("before_actor", "BACKUP_PIN_SHA_MISMATCH"),
    ("old_sha", "BACKUP_OLD_SHA_MISMATCH"),
    ("result_same", "BACKUP_PROMOTION_RESULT_INVALID"),
    ("current_pin", "BACKUP_DOES_NOT_MATCH_CURRENT_INSTALL"),
    ("task_pinned_elsewhere", "BACKUP_TASK_NOT_PINNED_TO_EXPECTED_RELEASE"),
])
def test_sha_mismatches_are_refused_before_any_write(install, mutate, code):
    other = "c" * 40
    backup = install["backup"]
    sha = install["old"]
    if mutate == "expected":
        sha = other
    elif mutate in ("before_pin", "before_actor"):
        name = "supervisor-pinned-release.before.json" if mutate == "before_pin" else "actor-local-promotion.before.json"
        data = json.loads((backup / name).read_text(encoding="utf-8"))
        data["accepted_sha"] = other
        (backup / name).write_text(json.dumps(data), encoding="utf-8")
    elif mutate in ("old_sha", "result_same"):
        value = other if mutate == "old_sha" else install["old"]
        data = {"old_sha": install["old"], "accepted_sha": NEW_SHA}
        data["old_sha" if mutate == "old_sha" else "accepted_sha"] = value
        (backup / "promotion-result.json").write_text(json.dumps(data), encoding="utf-8")
    elif mutate == "current_pin":
        path = install["state"] / "supervisor-pinned-release.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["accepted_sha"] = other
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        (backup / f"{WATCHDOG}.xml").write_text(
            TASK_XML.format(enabled="false", script="watch_pinned_continuous_flow.ps1", sha=other), encoding="utf-8")
    before = without(tree_hash(install["base"]), "flow-state\\IronGodControl")
    proc = run(install, apply=True, sha=sha)
    assert proc.returncode != 0 and code in (proc.stderr + proc.stdout)
    assert without(tree_hash(install["base"]), "flow-state\\IronGodControl") == before


def test_a_dirty_or_wrong_release_checkout_blocks_the_restore(install):
    (install["release"] / "stray.txt").write_text("x", encoding="utf-8")
    proc = run(install, apply=True)
    assert proc.returncode != 0 and "CHECKOUT_DIRTY" in (proc.stderr + proc.stdout)
    assert not restored(install)


def test_owner_control_must_be_held_and_is_never_changed(install):
    grant_owner_authority(install["state"])  # autonomy authorized: Take the Wheel is active
    control_before = tree_hash(install["state"] / "IronGodControl")
    before = without(tree_hash(install["base"]), "flow-state\\IronGodControl")
    for apply in (False, True):
        proc = run(install, apply=apply)
        assert proc.returncode != 0 and "OWNER_MUST_KEEP_CONTROL_DURING_RESTORE" in (proc.stderr + proc.stdout)
    assert without(tree_hash(install["base"]), "flow-state\\IronGodControl") == before
    assert tree_hash(install["state"] / "IronGodControl") == control_before


def test_unverified_owner_state_is_not_evidence_that_the_owner_holds_control(install):
    shutil.rmtree(install["state"] / "IronGodControl")  # no owner record at all: status is UNVERIFIED
    for apply in (False, True):
        proc = run(install, apply=apply)
        assert proc.returncode != 0 and "OWNER_MUST_KEEP_CONTROL_DURING_RESTORE" in (proc.stderr + proc.stdout)
    assert not restored(install)


def test_a_desktop_control_the_restore_created_is_removed_when_it_fails(install):
    (install["base"] / DESKTOP[1]).unlink()  # absent before the restore
    proc = run(install, "-InjectFailure", "AfterDesktop", apply=True)
    assert proc.returncode != 0
    assert not (install["base"] / DESKTOP[1]).exists()
    assert (install["base"] / DESKTOP[0]).read_text(encoding="utf-8") == f"new {DESKTOP[0]}\n"


def test_an_enabled_task_blocks_the_restore(install):
    (install["base"] / "task-sandbox" / f"{ACTOR}.state").write_text("Ready", encoding="utf-8")
    proc = run(install, apply=True)
    assert proc.returncode != 0 and "TASK_NOT_DISABLED" in (proc.stderr + proc.stdout)
    assert not restored(install)


@pytest.mark.parametrize("stage", ["AfterPins", "AfterTasks", "AfterDesktop"])
def test_an_interrupted_restore_reverts_and_never_reports_success(install, stage):
    before = without(tree_hash(install["base"]), "flow-state\\IronGodControl", "flow-state\\pre-restore-")
    proc = run(install, "-InjectFailure", stage, apply=True)
    assert proc.returncode != 0
    assert "PINNED_RELEASE_RESTORE_FAILED_REVERTED" in (proc.stderr + proc.stdout)
    assert "PINNED_RELEASE_RESTORED" not in proc.stdout  # an interrupted run is never reported as success
    assert without(tree_hash(install["base"]), "flow-state\\IronGodControl", "flow-state\\pre-restore-") == before
    snapshot = next(install["state"].glob("pre-restore-*"))
    assert (snapshot / "restore-failed.json").is_file() and not (snapshot / "restore-result.json").exists()
    assert run(install, apply=True).returncode == 0 and restored(install)  # and a clean retry succeeds


def test_a_restore_killed_part_way_is_finished_by_rerunning(install):
    """Simulate the process being terminated (Norton) after the first pin was restored: no success record exists."""
    shutil.copy(install["backup"] / "supervisor-pinned-release.before.json",
                install["state"] / "supervisor-pinned-release.json")
    assert not restored(install)
    proc = run(install, apply=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert restored(install)


def test_a_failed_recovery_is_reported_with_the_snapshot_and_no_success_record(install):
    pin = install["state"] / "supervisor-pinned-release.json"
    os.chmod(pin, stat.S_IREAD)  # the restore cannot replace it, and neither can the revert
    try:
        proc = run(install, apply=True)
    finally:
        os.chmod(pin, stat.S_IWRITE | stat.S_IREAD)
    text = proc.stderr + proc.stdout
    assert proc.returncode != 0 and "REVERT_ERRORS=" in text and "SNAPSHOT=" in text
    assert "SUPERVISOR_PIN" in text
    snapshot = next(install["state"].glob("pre-restore-*"))
    assert not (snapshot / "restore-result.json").exists()


def test_the_test_backend_is_refused_for_the_real_engine_root():
    real = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "ExponentialEngine"
    argv = [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
            "-ExpectedSha", "a" * 40, "-Base", str(real), "-TaskBackend", "Files"]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0 and "TEST_BACKEND_REFUSED_FOR_REAL_ENGINE_ROOT" in (proc.stderr + proc.stdout)


def test_the_script_never_starts_enables_or_stops_anything_and_has_a_valid_parse_tree():
    text = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("Start-ScheduledTask", "Enable-ScheduledTask", "Disable-ScheduledTask", "Stop-Process",
                      "Unregister-ScheduledTask", "human-mode.json"):
        assert forbidden not in text
    assert "(Get-IronGodOwnerStatus $state (Join-Path $release 'src')) -cne 'OWNER_CONTROL_ACTIVE'" in text
    # dry-run exits before the first write of any kind
    assert text.index("if (-not $Apply) {$plan|ConvertTo-Json -Depth 5;exit 0}") < text.index("New-Item -ItemType Directory")
    assert text.index("restore-result.json") > text.index("RESTORED_DESKTOP_CONTROL_UNVERIFIED")
    literal = str(SCRIPT).replace("'", "''")
    statement = ("$t=$null;$e=$null;[Management.Automation.Language.Parser]::ParseFile("
                 f"'{literal}',[ref]$t,[ref]$e)|Out-Null;if($e.Count){{exit 2}}else{{exit 0}}")
    done = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", statement],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
