from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_eval import DEFAULT_LEDGER, DEFAULT_SUITE, load_suite, record_attempt, validate_suite_files

RUNNER_SCHEMA_VERSION = 1
FORBIDDEN_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"})
PLACEHOLDERS = frozenset({"prompt_file", "worktree", "output_dir", "task_id", "evaluation_run_id"})
DEFAULT_OUTPUT_DIR = Path(".agent-eval-runs")


class AgentEvalRunnerError(RuntimeError):
    """Raised when the local agent-evaluation runner cannot proceed safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _slug(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    return safe.strip("-_") or "item"


def _is_exact_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdefABCDEF" for char in value)


def validate_runner_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentEvalRunnerError("runner config must be an object")
    required = {"schema_version", "configurations"}
    if set(raw) != required:
        raise AgentEvalRunnerError(f"runner config fields must be exactly {sorted(required)}")
    if raw["schema_version"] != RUNNER_SCHEMA_VERSION:
        raise AgentEvalRunnerError("runner config schema_version must be 1")
    configs = raw["configurations"]
    if not isinstance(configs, list) or len(configs) < 1:
        raise AgentEvalRunnerError("configurations must contain at least one agent configuration")

    normalized: list[dict[str, Any]] = []
    ids: set[tuple[str, str]] = set()
    for index, item in enumerate(configs):
        if not isinstance(item, dict):
            raise AgentEvalRunnerError(f"configuration {index} must be an object")
        fields = {"agent_id", "agent_config_id", "argv", "timeout_seconds"}
        if set(item) != fields:
            raise AgentEvalRunnerError(f"configuration {index} fields must be exactly {sorted(fields)}")
        if not _nonempty(item["agent_id"]) or not _nonempty(item["agent_config_id"]):
            raise AgentEvalRunnerError("agent_id and agent_config_id must be non-empty strings")
        key = (item["agent_id"], item["agent_config_id"])
        if key in ids:
            raise AgentEvalRunnerError(f"duplicate agent/config pair: {key}")
        ids.add(key)
        argv = item["argv"]
        if not isinstance(argv, list) or not argv or any(not _nonempty(token) for token in argv):
            raise AgentEvalRunnerError("argv must be a non-empty list of non-empty strings")
        executable = Path(argv[0]).name.lower()
        if executable in FORBIDDEN_SHELLS:
            raise AgentEvalRunnerError("shell executables are forbidden; configure the agent executable directly")
        timeout = item["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1 or timeout > 7200:
            raise AgentEvalRunnerError("timeout_seconds must be an integer from 1 through 7200")
        normalized.append(
            {
                "agent_id": item["agent_id"],
                "agent_config_id": item["agent_config_id"],
                "argv": list(argv),
                "timeout_seconds": timeout,
            }
        )
    return {"schema_version": RUNNER_SCHEMA_VERSION, "configurations": normalized}


def load_runner_config(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_runner_config(value)


def parse_repo_mapping(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name.strip() or not raw_path.strip():
        raise AgentEvalRunnerError("--repo must use owner/name=/path/to/checkout")
    return name.strip(), Path(raw_path).expanduser().resolve()


def _run_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started_at = _now()
    started = time.monotonic()
    timed_out = False
    returncode: int | None = None
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
            shell=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
    elapsed = time.monotonic() - started
    completed_at = _now()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "argv": argv,
                "started_at": started_at,
                "completed_at": completed_at,
                "elapsed_seconds": elapsed,
                "timed_out": timed_out,
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_seconds": elapsed,
        "timed_out": timed_out,
        "returncode": returncode,
        "success": (not timed_out and returncode == 0),
    }


def _git(root: Path, *args: str, timeout_seconds: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
        shell=False,
    )


def _logical_ref(matrix_id: str, agent_config_id: str, task_id: str, filename: str) -> str:
    return f"local-agent-eval:{matrix_id}/{_slug(agent_config_id)}/{_slug(task_id)}/{filename}"


def _expand_argv(argv: list[str], values: dict[str, str]) -> list[str]:
    expanded: list[str] = []
    for token in argv:
        value = token
        for key in PLACEHOLDERS:
            value = value.replace("{" + key + "}", values[key])
        expanded.append(value)
    return expanded


def _verification_argv(command: str) -> list[str]:
    argv = shlex.split(command, posix=True)
    if not argv:
        raise AgentEvalRunnerError("verification command cannot be empty")
    if Path(argv[0]).name.lower() in FORBIDDEN_SHELLS:
        raise AgentEvalRunnerError("verification commands may not invoke a shell executable")
    return argv


def _task_for_id(suite: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in suite["tasks"]:
        if task["task_id"] == task_id:
            return task
    raise AgentEvalRunnerError(f"unknown task_id: {task_id}")


def _verify_baseline(repo_root: Path, baseline_ref: str) -> None:
    if not _is_exact_sha(baseline_ref):
        raise AgentEvalRunnerError("task baseline_ref must remain an exact 40-character SHA")
    completed = _git(repo_root, "cat-file", "-e", f"{baseline_ref}^{{commit}}")
    if completed.returncode != 0:
        raise AgentEvalRunnerError(f"baseline commit is unavailable in {repo_root}: {baseline_ref}")


def _remove_worktree(repo_root: Path, worktree: Path) -> None:
    if worktree.exists():
        _git(repo_root, "worktree", "remove", "--force", str(worktree))
    _git(repo_root, "worktree", "prune")


def run_task_attempt(
    suite: dict[str, Any],
    task: dict[str, Any],
    agent_config: dict[str, Any],
    *,
    repo_root: Path,
    output_dir: Path,
    matrix_id: str,
    ledger: str | Path = DEFAULT_LEDGER,
    record: bool = True,
) -> dict[str, Any]:
    """Run one deterministic benchmark in a disposable exact-baseline worktree."""
    _verify_baseline(repo_root, task["baseline_ref"])
    fixture = (repo_root / task["fault_fixture_ref"]).resolve()
    if not fixture.is_file():
        raise AgentEvalRunnerError(f"fault fixture not found: {fixture}")

    attempt_id = f"{matrix_id}:{agent_config['agent_config_id']}:{task['task_id']}"
    artifact_dir = output_dir / _slug(agent_config["agent_config_id"]) / _slug(task["task_id"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    worktree_parent = Path(tempfile.mkdtemp(prefix="project-orchestrator agent-eval-"))
    worktree = worktree_parent / "worktree"

    result: dict[str, Any] = {
        "task_id": task["task_id"],
        "agent_id": agent_config["agent_id"],
        "agent_config_id": agent_config["agent_config_id"],
        "evaluation_run_id": attempt_id,
        "status": "UNKNOWN",
        "acceptance": None,
        "recorded_event_count": 0,
        "artifact_dir": str(artifact_dir),
    }

    try:
        added = _git(repo_root, "worktree", "add", "--detach", str(worktree), task["baseline_ref"])
        if added.returncode != 0:
            raise AgentEvalRunnerError(f"git worktree add failed: {added.stderr.strip() or added.stdout.strip()}")
        head = _git(worktree, "rev-parse", "HEAD")
        actual_head = head.stdout.strip() if head.returncode == 0 else "UNKNOWN"
        if actual_head.lower() != task["baseline_ref"].lower():
            raise AgentEvalRunnerError(
                f"disposable worktree head mismatch: expected {task['baseline_ref']}, got {actual_head}"
            )

        checked = _git(worktree, "apply", "--check", str(fixture))
        if checked.returncode != 0:
            raise AgentEvalRunnerError(f"fault fixture does not apply cleanly: {checked.stderr.strip()}")
        applied = _git(worktree, "apply", str(fixture))
        if applied.returncode != 0:
            raise AgentEvalRunnerError(f"fault fixture apply failed: {applied.stderr.strip()}")

        fault_log = artifact_dir / "fault-red-proof.json"
        first_verification = _verification_argv(task["verification_commands"][0])
        fault_result = _run_command(
            first_verification,
            cwd=worktree,
            timeout_seconds=min(agent_config["timeout_seconds"], 600),
            log_path=fault_log,
        )
        if fault_result["success"]:
            result["status"] = "INVALID_BENCHMARK"
            result["reason"] = "fault fixture did not make the first deterministic verification command fail"
            return result

        prompt_file = worktree / ".agent-eval-prompt.txt"
        prompt_file.write_text(task["prompt"].rstrip() + "\n", encoding="utf-8")
        values = {
            "prompt_file": str(prompt_file),
            "worktree": str(worktree),
            "output_dir": str(artifact_dir),
            "task_id": task["task_id"],
            "evaluation_run_id": attempt_id,
        }
        agent_argv = _expand_argv(agent_config["argv"], values)
        agent_env = os.environ.copy()
        agent_env.update(
            {
                "AGENT_EVAL_PROMPT_FILE": str(prompt_file),
                "AGENT_EVAL_WORKTREE": str(worktree),
                "AGENT_EVAL_OUTPUT_DIR": str(artifact_dir),
                "AGENT_EVAL_TASK_ID": task["task_id"],
                "AGENT_EVAL_RUN_ID": attempt_id,
                "AGENT_EVAL_BASELINE_REF": task["baseline_ref"],
            }
        )
        attempt_started = _now()
        agent_log = artifact_dir / "agent.json"
        agent_result = _run_command(
            agent_argv,
            cwd=worktree,
            timeout_seconds=agent_config["timeout_seconds"],
            log_path=agent_log,
            env=agent_env,
        )

        verification_refs: list[str] = []
        verification_ok = True
        verification_results: list[dict[str, Any]] = []
        for index, command in enumerate(task["verification_commands"], start=1):
            log_name = f"verification-{index}.json"
            log_path = artifact_dir / log_name
            command_result = _run_command(
                _verification_argv(command),
                cwd=worktree,
                timeout_seconds=min(agent_config["timeout_seconds"], 1200),
                log_path=log_path,
            )
            verification_ok = verification_ok and command_result["success"]
            verification_results.append({"command": command, **command_result})
            verification_refs.append(
                _logical_ref(matrix_id, agent_config["agent_config_id"], task["task_id"], log_name)
            )
            if not command_result["success"]:
                break

        observed_at = verification_results[-1]["completed_at"] if verification_results else _now()
        runner_manifest_name = "runner-result.json"
        runner_manifest_ref = _logical_ref(
            matrix_id, agent_config["agent_config_id"], task["task_id"], runner_manifest_name
        )
        base_refs = [
            _logical_ref(matrix_id, agent_config["agent_config_id"], task["task_id"], "fault-red-proof.json"),
            _logical_ref(matrix_id, agent_config["agent_config_id"], task["task_id"], "agent.json"),
            runner_manifest_ref,
        ]
        acceptance_outcome = "PASS" if verification_ok else "FAIL"
        attempt = {
            "schema_version": 1,
            "suite_id": suite["suite_id"],
            "task_id": task["task_id"],
            "evaluation_run_id": attempt_id,
            "agent_id": agent_config["agent_id"],
            "agent_config_id": agent_config["agent_config_id"],
            "actor": "EXTERNAL_AUTOMATION",
            "repository": task["repository"],
            "baseline_ref": task["baseline_ref"],
            "measurement_contract_ref": task["measurement_contract_ref"],
            "started_at": attempt_started,
            "checks": [
                {
                    "stage": "PREFLIGHT",
                    "attempt": 1,
                    "observed_at": observed_at,
                    "outcome": acceptance_outcome,
                    "evidence_refs": verification_refs,
                }
            ],
            "repairs": [],
            "human_interventions": [],
            "acceptance": {
                "observed_at": observed_at,
                "outcome": acceptance_outcome,
                "evidence_refs": verification_refs,
            },
            "measured_cost": None,
            "evidence_refs": sorted(set(base_refs + verification_refs)),
        }
        attempt_path = artifact_dir / "attempt.json"
        attempt_path.write_text(json.dumps(attempt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        recorded = record_attempt(attempt, suite, ledger=ledger) if record else []

        manifest = {
            "schema_version": 1,
            "matrix_id": matrix_id,
            "evaluation_run_id": attempt_id,
            "repository": task["repository"],
            "baseline_ref": task["baseline_ref"],
            "worktree_head": actual_head,
            "task_id": task["task_id"],
            "agent_id": agent_config["agent_id"],
            "agent_config_id": agent_config["agent_config_id"],
            "fault_red_proved": True,
            "agent_invocation": agent_result,
            "verification": verification_results,
            "acceptance": acceptance_outcome,
            "recorded_event_count": len(recorded),
            "product_reality_inferred": False,
            "merge_authority": False,
        }
        (artifact_dir / runner_manifest_name).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        result.update(
            {
                "status": "COMPLETED",
                "acceptance": acceptance_outcome,
                "agent_timed_out": agent_result["timed_out"],
                "agent_returncode": agent_result["returncode"],
                "recorded_event_count": len(recorded),
            }
        )
        return result
    finally:
        _remove_worktree(repo_root, worktree)
        shutil.rmtree(worktree_parent, ignore_errors=True)


def run_matrix(
    suite: dict[str, Any],
    runner_config: dict[str, Any],
    *,
    repo_roots: dict[str, Path],
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    ledger: str | Path = DEFAULT_LEDGER,
    matrix_id: str | None = None,
    record: bool = True,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    suite = validate_suite_files(suite)
    config = validate_runner_config(runner_config)
    selected = suite["tasks"] if not task_ids else [_task_for_id(suite, task_id) for task_id in task_ids]
    missing_roots = sorted({task["repository"] for task in selected} - set(repo_roots))
    if missing_roots:
        raise AgentEvalRunnerError(f"missing local repository mappings: {missing_roots}")

    matrix_id = matrix_id or f"agent-eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    root_output = Path(output_dir).expanduser().resolve() / _slug(matrix_id)
    root_output.mkdir(parents=True, exist_ok=True)
    started_at = _now()
    results: list[dict[str, Any]] = []

    for agent_config in config["configurations"]:
        for task in selected:
            repo_root = Path(repo_roots[task["repository"]]).expanduser().resolve()
            if not repo_root.is_dir():
                raise AgentEvalRunnerError(f"repository root is unavailable: {repo_root}")
            results.append(
                run_task_attempt(
                    suite,
                    task,
                    agent_config,
                    repo_root=repo_root,
                    output_dir=root_output,
                    matrix_id=matrix_id,
                    ledger=ledger,
                    record=record,
                )
            )

    completed = [item for item in results if item["status"] == "COMPLETED"]
    invalid = [item for item in results if item["status"] == "INVALID_BENCHMARK"]
    summary = {
        "schema_version": 1,
        "matrix_id": matrix_id,
        "suite_id": suite["suite_id"],
        "started_at": started_at,
        "completed_at": _now(),
        "recorded_to_kpi": record,
        "product_reality_inferred": False,
        "merge_authority": False,
        "configurations": len(config["configurations"]),
        "tasks": len(selected),
        "attempts": len(results),
        "completed_attempts": len(completed),
        "invalid_benchmarks": len(invalid),
        "pass_count": sum(1 for item in completed if item["acceptance"] == "PASS"),
        "fail_count": sum(1 for item in completed if item["acceptance"] == "FAIL"),
        "results": results,
    }
    (root_output / "matrix-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator agent-eval-runner")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-config")
    validate.add_argument("config", type=Path)

    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    run.add_argument("--repo", action="append", default=[], help="owner/name=/path/to/local/checkout")
    run.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    run.add_argument("--matrix-id")
    run.add_argument("--task", action="append", default=[])
    run.add_argument("--no-record", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "validate-config":
        config = load_runner_config(args.config)
        _print(
            {
                "schema_version": RUNNER_SCHEMA_VERSION,
                "status": "GREEN",
                "configuration_count": len(config["configurations"]),
                "shell_execution": False,
                "product_reality_inferred": False,
                "merge_authority": False,
            }
        )
        return 0

    suite = load_suite(args.suite)
    config = load_runner_config(args.config)
    repo_roots = dict(parse_repo_mapping(value) for value in args.repo)
    try:
        summary = run_matrix(
            suite,
            config,
            repo_roots=repo_roots,
            output_dir=args.output_dir,
            ledger=args.ledger,
            matrix_id=args.matrix_id,
            record=not args.no_record,
            task_ids=args.task or None,
        )
    except AgentEvalRunnerError as exc:
        _print({"status": "FAILED", "error": str(exc)})
        return 2
    _print(summary)
    return 0 if summary["invalid_benchmarks"] == 0 else 2
