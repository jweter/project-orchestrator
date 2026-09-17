import importlib.util
import json
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("laptop_actor_runner", Path(__file__).parents[1] / "scripts" / "laptop_actor_runner.py")
runner = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(runner)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def test_runner_executes_exact_base_and_writes_result(tmp_path):
    workspace_root = tmp_path / "work"; repo = workspace_root / "Project-Everward"; repo.mkdir(parents=True)
    git(repo, "init"); git(repo, "config", "user.email", "test@example.com"); git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("ok\n"); git(repo, "add", "README.md"); git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    queue = tmp_path / "queue"; inbox = queue / "inbox"; outbox = queue / "outbox"; inbox.mkdir(parents=True); outbox.mkdir()
    job = {"job_id":"abc", "packet_id":"p1", "repository":"jweter/Project-Everward", "branch":"worker/p1", "base_sha":base, "verification":["git status --porcelain"]}
    path = inbox / "abc.job.json"; path.write_text(json.dumps(job))
    runner.execute(path, workspace_root, outbox)
    result = json.loads((outbox / "abc.result.json").read_text())
    assert result["status"] == "PASS"
    assert result["exact_head_sha"] == base
    assert (inbox / "abc.job.json.done").exists()


def test_runner_fails_closed_on_dirty_worktree(tmp_path):
    workspace_root = tmp_path / "work"; repo = workspace_root / "Project-Everward"; repo.mkdir(parents=True)
    git(repo, "init"); git(repo, "config", "user.email", "test@example.com"); git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("ok\n"); git(repo, "add", "README.md"); git(repo, "commit", "-m", "base"); base = git(repo, "rev-parse", "HEAD")
    (repo / "dirty.txt").write_text("do not overwrite")
    queue = tmp_path / "queue"; inbox = queue / "inbox"; outbox = queue / "outbox"; inbox.mkdir(parents=True); outbox.mkdir()
    job = {"job_id":"dirty", "packet_id":"p2", "repository":"jweter/Project-Everward", "branch":"worker/p2", "base_sha":base, "verification":[]}
    path = inbox / "dirty.job.json"; path.write_text(json.dumps(job))
    runner.execute(path, workspace_root, outbox)
    result = json.loads((outbox / "dirty.result.json").read_text())
    assert result["status"] == "ENVIRONMENT_FAILURE"
    assert "clean" in result["verification"][0]["runner_error"]
