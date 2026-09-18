import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "laptop_actor_runner",
    Path(__file__).parents[1] / "scripts" / "laptop_actor_runner.py",
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def make_repo(path: Path) -> str:
    path.mkdir(parents=True)
    git(path, "init")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("ok\n")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "base")
    return git(path, "rev-parse", "HEAD")


def write_job(inbox: Path, name: str, job: dict) -> Path:
    path = inbox / f"{name}.job.json"
    path.write_text(json.dumps(job))
    return path


def test_runner_executes_exact_base_and_writes_result(tmp_path, monkeypatch):
    workspace_root = tmp_path / "work"
    repo = workspace_root / "Project-Everward"
    base = make_repo(repo)
    monkeypatch.setattr(runner, "_validate_workspace_origin", lambda *_: None)

    queue = tmp_path / "queue"
    inbox, outbox = queue / "inbox", queue / "outbox"
    inbox.mkdir(parents=True)
    outbox.mkdir()
    job = {
        "job_id": "abc",
        "packet_id": "p1",
        "repository": "jweter/Project-Everward",
        "branch": "worker/p1",
        "base_sha": base,
        "verification": ["git status --porcelain"],
    }
    path = write_job(inbox, "abc", job)
    runner.execute(path, workspace_root, outbox)

    result = json.loads((outbox / "abc.result.json").read_text())
    assert result["status"] == "PASS"
    assert result["exact_head_sha"] == base
    assert result["failure_class"] is None
    assert (inbox / "abc.job.json.done").exists()


def test_generic_verification_uses_direct_argv_without_shell(tmp_path, monkeypatch):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    status, verification, failure = runner._run_generic_verification(
        {"verification": ["python -m pytest -q"]}, tmp_path
    )
    assert status == "PASS"
    assert failure is None
    assert calls[0][0] == ["python", "-m", "pytest", "-q"]
    assert calls[0][1]["shell"] is False


def test_runner_fails_closed_on_dirty_tracked_worktree(tmp_path, monkeypatch):
    workspace_root = tmp_path / "work"
    repo = workspace_root / "Project-Everward"
    base = make_repo(repo)
    (repo / "README.md").write_text("modified\n")
    monkeypatch.setattr(runner, "_validate_workspace_origin", lambda *_: None)

    queue = tmp_path / "queue"
    inbox, outbox = queue / "inbox", queue / "outbox"
    inbox.mkdir(parents=True)
    outbox.mkdir()
    job = {
        "job_id": "dirty",
        "packet_id": "p2",
        "repository": "jweter/Project-Everward",
        "branch": "worker/p2",
        "base_sha": base,
        "verification": ["git status --porcelain"],
    }
    path = write_job(inbox, "dirty", job)
    runner.execute(path, workspace_root, outbox)

    result = json.loads((outbox / "dirty.result.json").read_text())
    assert result["status"] == "ENVIRONMENT_FAILURE"
    assert result["failure_class"] == "TRACKED_WORKTREE_DIRTY"
    assert result["verification"] == [{"runner_error_code": "TRACKED_WORKTREE_DIRTY"}]


def test_missing_workspace_is_provisioned_inside_actor_root(tmp_path, monkeypatch):
    workspace_root = tmp_path / "managed"

    def fake_clone(repository, workspace):
        assert repository == "jweter/knowledge-engine-core"
        assert workspace == workspace_root / "knowledge-engine-core"
        make_repo(workspace)

    monkeypatch.setattr(runner, "_clone_workspace", fake_clone)
    monkeypatch.setattr(runner, "_validate_workspace_origin", lambda *_: None)

    workspace = runner.ensure_managed_workspace(
        {"repository": "jweter/knowledge-engine-core"},
        workspace_root,
    )
    assert workspace == workspace_root / "knowledge-engine-core"
    assert (workspace / ".git").is_dir()


def test_workspace_origin_mismatch_fails_closed(tmp_path, monkeypatch):
    repo = tmp_path / "knowledge-engine-core"
    make_repo(repo)

    def fake_run(cmd, cwd):
        if cmd[:4] == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                cmd, 0, "https://github.com/other/repo.git\n", ""
            )
        raise AssertionError(cmd)

    monkeypatch.setattr(runner, "run", fake_run)
    with pytest.raises(runner.ActorEnvironmentError, match="ORIGIN_MISMATCH"):
        runner._validate_workspace_origin(repo, "jweter/knowledge-engine-core")


def test_ke_native_adapter_requires_authorized_checks():
    with pytest.raises(runner.ActorEnvironmentError, match="NATIVE_CHECK_NOT_AUTHORIZED"):
        runner._validate_ke_checks(
            {"requested_checks": ["preflight", "arbitrary_shell"]}
        )


def test_ke_native_adapter_maps_exact_sanitized_result(tmp_path, monkeypatch):
    workspace = tmp_path / "knowledge-engine-core"
    workspace.mkdir()
    outbox = tmp_path / "queue" / "outbox"
    outbox.mkdir(parents=True)
    base = "a" * 40
    job = {
        "job_id": "ke-1",
        "packet_id": "p-ke",
        "repository": "jweter/knowledge-engine-core",
        "branch": "laptop/ke-1",
        "base_sha": base,
        "execution_adapter": "knowledge_engine_core",
        "requested_checks": ["preflight", "ollama_health"],
    }
    monkeypatch.setattr(runner, "_ensure_ke_environment", lambda _: "poetry")

    def fake_subprocess_run(args, **kwargs):
        result_index = args.index("--result") + 1
        result_path = Path(args[result_index])
        result_path.write_text(
            json.dumps(
                {
                    "request_id": "ke-1",
                    "repository": "jweter/knowledge-engine-core",
                    "exact_sha": base,
                    "environment_id": runner.KNOWLEDGE_ENGINE_ENVIRONMENT_ID,
                    "status": "PASS",
                    "completed_at_utc": "2026-09-17T00:00:00+00:00",
                    "summary": "sanitized",
                    "artifact_refs": [],
                    "failure_class": None,
                }
            )
        )
        assert kwargs["check"] is False
        assert kwargs.get("shell") is None
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_subprocess_run)
    status, evidence, failure = runner._run_ke_native_worker(job, workspace, outbox)
    assert status == "PASS"
    assert failure is None
    assert evidence == [
        {
            "adapter": "knowledge_engine_core",
            "checks": ["preflight", "ollama_health"],
            "status": "PASS",
            "failure_class": None,
            "process_returncode": 0,
        }
    ]


def test_ke_native_adapter_rejects_result_identity_mismatch(tmp_path, monkeypatch):
    workspace = tmp_path / "knowledge-engine-core"
    workspace.mkdir()
    outbox = tmp_path / "queue" / "outbox"
    outbox.mkdir(parents=True)
    job = {
        "job_id": "ke-2",
        "repository": "jweter/knowledge-engine-core",
        "branch": "laptop/ke-2",
        "base_sha": "a" * 40,
        "requested_checks": ["preflight"],
    }
    monkeypatch.setattr(runner, "_ensure_ke_environment", lambda _: "poetry")

    def fake_subprocess_run(args, **kwargs):
        result_path = Path(args[args.index("--result") + 1])
        result_path.write_text(
            json.dumps(
                {
                    "request_id": "ke-2",
                    "repository": "jweter/knowledge-engine-core",
                    "exact_sha": "b" * 40,
                    "environment_id": runner.KNOWLEDGE_ENGINE_ENVIRONMENT_ID,
                    "status": "PASS",
                }
            )
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_subprocess_run)
    with pytest.raises(
        runner.ActorEnvironmentError, match="NATIVE_WORKER_IDENTITY_MISMATCH"
    ):
        runner._run_ke_native_worker(job, workspace, outbox)


def test_claim_job_is_atomic(tmp_path):
    job = tmp_path / "a.job.json"
    job.write_text("{}", encoding="utf-8")
    claimed = runner._claim_job(job)
    assert not job.exists()
    assert claimed.name == "a.job.json.running"
    assert claimed.exists()


def test_recover_interrupted_job(tmp_path):
    interrupted = tmp_path / "a.job.json.running"
    interrupted.write_text("{}", encoding="utf-8")
    assert runner.recover_interrupted_jobs(tmp_path) == 1
    assert (tmp_path / "a.job.json").exists()
    assert not interrupted.exists()


def test_recovery_fails_closed_on_collision(tmp_path):
    (tmp_path / "a.job.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a.job.json.running").write_text("{}", encoding="utf-8")
    with pytest.raises(runner.ActorEnvironmentError, match="INTERRUPTED_JOB_COLLISION"):
        runner.recover_interrupted_jobs(tmp_path)
