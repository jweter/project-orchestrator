"""Product worktree cleanup: archive and verify evidence first; keep anything active, open or unclassifiable."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from project_orchestrator import iron_god_worktree_cleanup as cleanup


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def lane(tmp_path: Path):
    state = tmp_path / "EE" / "flow-state"
    root = state / "iron-god-products"
    (root / "packets").mkdir(parents=True)
    (root / "handbacks").mkdir()
    clone = tmp_path / "EE" / "canon-workspaces" / "products" / "repo"
    clone.mkdir(parents=True)
    git(clone, "init", "-q", "-b", "main")
    git(clone, "config", "user.email", "o@example.com")
    git(clone, "config", "user.name", "Owner")
    (clone / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(clone, "add", ".")
    git(clone, "commit", "-q", "-m", "base")
    return state, root, clone, git(clone, "rev-parse", "HEAD")


def turn(state, root, clone, base, pid, *, handback=True, commit=False, dirty=False, publication=None):
    worktree = clone.parent / "worktrees" / pid
    branch = f"iron-god/repo/{pid.lower()}"
    git(clone, "worktree", "add", "-q", "-b", branch, str(worktree), base)
    (root / "packets" / f"{pid}.json").write_text(json.dumps({"branch": branch, "base_sha": base}), encoding="utf-8")
    if handback:
        (root / "handbacks" / f"{pid}.json").write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")
    if commit:
        (worktree / "b.py").write_text("y = 2\n", encoding="utf-8")
        git(worktree, "add", ".")
        git(worktree, "-c", "user.name=w", "-c", "user.email=w@x", "commit", "-q", "-m", "work")
    if dirty:
        (worktree / "a.py").write_text("x = 3\n", encoding="utf-8")
        (worktree / "new.py").write_text("z = 4\n", encoding="utf-8")
    if publication:
        path = root / "publications.json"
        items = json.loads(path.read_text(encoding="utf-8"))["items"] if path.exists() else {}
        items[pid] = {"state": publication}
        path.write_text(json.dumps({"items": items}), encoding="utf-8")
    return worktree


def test_only_finished_turns_are_eligible_and_open_or_unknown_work_is_kept(tmp_path):
    state, root, clone, base = lane(tmp_path)
    turn(state, root, clone, base, "P-FAILED")
    turn(state, root, clone, base, "P-ACTIVE")
    (root / "active.json").write_text(json.dumps({"packet_id": "P-ACTIVE"}), encoding="utf-8")
    turn(state, root, clone, base, "P-CI", commit=True, publication="AWAITING_CI")
    turn(state, root, clone, base, "P-REVIEW", publication="AWAITING_OWNER_REVIEW")
    turn(state, root, clone, base, "P-NOHANDBACK", handback=False)
    classes = {row["packet_id"]: row["class"] for row in cleanup.plan(state)}
    assert classes == {"P-FAILED": "FAILED_NO_COMMITS", "P-ACTIVE": "ACTIVE", "P-CI": "PUBLICATION_OPEN",
                       "P-REVIEW": "PUBLICATION_OPEN", "P-NOHANDBACK": "UNKNOWN"}
    assert {k for k, v in classes.items() if v in cleanup.REMOVABLE} == {"P-FAILED"}


def test_uncommitted_work_is_archived_and_verified_before_removal(tmp_path):
    state, root, clone, base = lane(tmp_path)
    worktree = turn(state, root, clone, base, "P-DIRTY", dirty=True)
    row = next(r for r in cleanup.plan(state) if r["packet_id"] == "P-DIRTY")
    assert row["class"] == "UNCOMMITTED_CHANGES"
    exact = (worktree / "new.py").read_bytes()  # archive keeps exact bytes (CRLF on Windows)
    result = cleanup.remove_one(state, row)
    assert result["result"] == "REMOVED" and not worktree.exists()
    archive = json.loads(Path(result["archive"]).read_text(encoding="utf-8"))
    assert "-x = 1" in archive["diff_against_head"] and "+x = 3" in archive["diff_against_head"]
    assert archive["untracked"] == {"new.py": exact.decode("utf-8")}


def test_committed_work_stays_reachable_on_its_branch_after_removal(tmp_path):
    state, root, clone, base = lane(tmp_path)
    worktree = turn(state, root, clone, base, "P-COMMIT", commit=True)
    row = next(r for r in cleanup.plan(state) if r["packet_id"] == "P-COMMIT")
    assert row["class"] == "UNPUBLISHED_COMMITS" and row["commits_ahead"] == 1
    result = cleanup.remove_one(state, row)
    assert result["result"] == "REMOVED" and not worktree.exists()
    assert git(clone, "rev-parse", f"refs/heads/{row['branch']}") == row["head_sha"]


def test_committed_work_without_a_branch_ref_is_kept(tmp_path):
    state, root, clone, base = lane(tmp_path)
    worktree = turn(state, root, clone, base, "P-DETACHED", commit=True)
    row = next(r for r in cleanup.plan(state) if r["packet_id"] == "P-DETACHED")
    git(worktree, "checkout", "-q", "--detach")
    git(clone, "branch", "-D", row["branch"])
    assert cleanup.remove_one(state, row)["result"] == "KEPT_BRANCH_REF_DOES_NOT_HOLD_THE_COMMITS"
    assert worktree.exists()


def test_apply_refuses_unless_the_owner_holds_control(tmp_path, monkeypatch, capsys):
    state, root, clone, base = lane(tmp_path)
    worktree = turn(state, root, clone, base, "P-FAILED")
    from project_orchestrator import iron_god_owner_control as owner_controller
    monkeypatch.setattr(owner_controller, "owner_status", lambda _root: "AUTHORIZED")
    assert cleanup.main(["--state-root", str(state), "--apply"]) == 3
    assert "OWNER_MUST_HOLD_CONTROL" in capsys.readouterr().out and worktree.exists()


def test_binary_work_is_archived_losslessly_before_removal(tmp_path):
    """Regression (PR #586 review): binary untracked files and binary edits of tracked
    files must be recoverable from the archive, not reduced to a placeholder."""
    import base64

    state, root, clone, base = lane(tmp_path)
    worktree = turn(state, root, clone, base, "P-BINARY")
    blob = bytes(range(256)) * 4
    (worktree / "model.bin").write_bytes(blob)
    (worktree / "a.py").write_bytes(b"\xff\xfe binary edit \x00\x01")
    row = next(r for r in cleanup.plan(state) if r["packet_id"] == "P-BINARY")
    result = cleanup.remove_one(state, row)
    assert result["result"] == "REMOVED" and not worktree.exists()
    archive = json.loads(Path(result["archive"]).read_text(encoding="utf-8"))
    assert base64.b64decode(archive["untracked"]["model.bin"]["base64"]) == blob
    assert "GIT binary patch" in archive["diff_against_head"]
    assert archive["unreadable"] == []


def test_unreadable_untracked_file_keeps_the_worktree(tmp_path, monkeypatch):
    state, root, clone, base = lane(tmp_path)
    worktree = turn(state, root, clone, base, "P-LOCKED")
    (worktree / "locked.dat").write_bytes(b"cannot be archived")
    original = Path.read_bytes

    def refuse(self):
        if self.name == "locked.dat":
            raise PermissionError("locked")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    row = next(r for r in cleanup.plan(state) if r["packet_id"] == "P-LOCKED")
    result = cleanup.remove_one(state, row)
    assert result["result"] == "KEPT_UNREADABLE_UNTRACKED_FILE"
    assert result["unreadable"] == ["locked.dat"] and worktree.exists()


# ---- the engine cleans up after itself (owner, 2026-11-14) ---------------------------------------------------------------

def test_auto_clean_removes_finished_turns_keeps_the_rest_and_waits_for_its_interval(tmp_path):
    state, root, clone, base = lane(tmp_path)
    failed = turn(state, root, clone, base, "P-FAILED", dirty=True)
    active = turn(state, root, clone, base, "P-ACTIVE")
    (root / "active.json").write_text(json.dumps({"packet_id": "P-ACTIVE"}), encoding="utf-8")
    review = turn(state, root, clone, base, "P-CI", commit=True, publication="AWAITING_CI")

    report = cleanup.auto_clean(state, now=1000.0)

    assert report["removed"] == 1 and report["results"] == {"P-FAILED": "REMOVED"}
    assert report["kept"] == {"P-ACTIVE": "ACTIVE", "P-CI": "PUBLICATION_OPEN"}
    assert not failed.exists() and active.exists() and review.exists()
    assert (root / "reports" / "worktree-archive" / "P-FAILED.json").is_file()   # evidence first, as with --apply

    later = turn(state, root, clone, base, "P-LATER")
    assert cleanup.auto_clean(state, now=1000.0 + cleanup.AUTO_INTERVAL_S - 1) is None and later.exists()
    assert cleanup.auto_clean(state, now=1000.0 + cleanup.AUTO_INTERVAL_S)["removed"] == 1 and not later.exists()


def test_auto_clean_is_bounded_per_run_and_finishes_on_later_runs(tmp_path):
    state, root, clone, base = lane(tmp_path)
    trees = [turn(state, root, clone, base, f"P-{n}") for n in range(3)]

    first = cleanup.auto_clean(state, now=1.0, interval=0, limit=2)
    assert first["removed"] == 2 and first["left_for_next_run"] == 1 and sum(t.exists() for t in trees) == 1
    assert cleanup.auto_clean(state, now=2.0, interval=0, limit=2)["removed"] == 1 and not any(t.exists() for t in trees)


def _capable(state, root, clone, base, ident, status, *, dirty=True, job=True):
    worktree = clone.parent / "worktrees" / f"capable-{ident}"
    branch = f"iron-god/capable/{ident}"
    git(clone, "worktree", "add", "-q", "-b", branch, str(worktree), base)
    if dirty:
        (worktree / "half.py").write_text("unfinished = True\n", encoding="utf-8")
    if job:
        (root / "capable-engine-jobs").mkdir(exist_ok=True)
        (root / "capable-engine-jobs" / f"{ident}.job.json").write_text(
            json.dumps({"branch": branch, "base": base}), encoding="utf-8")
    if status:
        (root / "capable-dispatches").mkdir(exist_ok=True)
        (root / "capable-dispatches" / f"{ident}.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    return worktree


def test_a_capable_builds_leftover_worktree_is_removed_only_once_that_build_is_over(tmp_path):
    """An owner takeover kills the in-engine runner before it can remove its own worktree."""
    state, root, clone, base = lane(tmp_path)
    running = _capable(state, root, clone, base, "aaa", "DISPATCHED")
    interrupted = _capable(state, root, clone, base, "bbb", "INTERRUPTED")
    failed = _capable(state, root, clone, base, "ccc", "FAIL", dirty=False)
    no_row = _capable(state, root, clone, base, "ddd", None)
    no_job = _capable(state, root, clone, base, "eee", "FAIL", job=False)

    report = cleanup.auto_clean(state, now=1.0)

    assert report["results"] == {"capable-bbb": "REMOVED", "capable-ccc": "REMOVED"}
    assert report["kept"] == {"capable-aaa": "ACTIVE", "capable-ddd": "UNKNOWN", "capable-eee": "UNKNOWN"}
    assert running.exists() and no_row.exists() and no_job.exists() and not interrupted.exists() and not failed.exists()
    archived = json.loads((root / "reports" / "worktree-archive" / "capable-bbb.json").read_text(encoding="utf-8"))
    assert archived["untracked"]["half.py"].strip() == "unfinished = True"   # the unfinished work is kept as evidence
