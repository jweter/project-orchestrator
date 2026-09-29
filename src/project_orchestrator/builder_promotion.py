from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from .state_io import atomic_json


def promote_candidate(
    *,
    ws: Path,
    state_path: Path,
    repo: str,
    idx: int,
    main_branch: str,
    branch: str,
    base: str,
    head: str,
    paths: list[str],
    slice_authority: dict[str, Any] | None,
    execution_grant: Path | None,
    run_exact_head_preflight_fn: Callable,
    reset_workspace_fn: Callable,
    retain_failure_lesson_fn: Callable,
    bind_preflight_evidence_fn: Callable,
    review_candidate_fn: Callable,
    advance_grant_stage_fn: Callable,
    refresh_base_before_publish_fn: Callable,
    changed_paths_fn: Callable,
    generated_change_failure_fn: Callable,
    run_fn: Callable,
    pr_number_from_url_fn: Callable,
    verified_repair: dict[str, str] | None = None,
    record_verified_repair_fn: Callable | None = None,
    promote_verified_repairs_fn: Callable | None = None,
) -> dict[str, Any]:
    """Verify, rebase-refresh, publish, and open a PR for one committed candidate."""
    preflight, verified_head, head_stable = run_exact_head_preflight_fn(ws, head)
    if preflight.returncode != 0 or not head_stable:
        reset_workspace_fn(ws, base)
        detail = (
            preflight.stderr
            or preflight.stdout
            or (
                f"HEAD_CHANGED_DURING_PREFLIGHT:{head}:{verified_head}"
                if verified_head != head
                else f"returncode={preflight.returncode}"
            )
        )
        lesson = retain_failure_lesson_fn(
            state_path, "preflight", detail, "PREFLIGHT_FAILED"
        )
        result = {
            "status": "PREFLIGHT_FAILED",
            "repository": repo,
            "returncode": preflight.returncode,
            "verified_head_sha": verified_head,
            "expected_head_sha": head,
            "failure_fingerprint": lesson["fingerprint"],
            "last_repo_index": idx,
            "last_repository": repo,
        }
        atomic_json(state_path, result)
        return result

    grant_id = None
    evidence_binding = None
    if execution_grant is not None:
        try:
            grant_id = json.loads(
                execution_grant.read_text(encoding="utf-8")
            )["grant_id"]
            evidence_binding = bind_preflight_evidence_fn(
                ws, base, head, grant_id
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, RuntimeError) as exc:
            reset_workspace_fn(ws, base)
            result = {
                "status": "PREFLIGHT_EVIDENCE_REJECTED",
                "repository": repo,
                "review_reason": str(exc),
                "head_sha": head,
                "last_repo_index": idx,
                "last_repository": repo,
            }
            atomic_json(state_path, result)
            return result

        verdict = review_candidate_fn(
            ws,
            base,
            head,
            slice_authority,
            paths,
            preflight.returncode,
            evidence_binding=evidence_binding,
            grant_id=grant_id,
        )
        if verdict.status != "APPROVE":
            reset_workspace_fn(ws, base)
            result = {
                "status": "INDEPENDENT_REVIEW_REJECTED",
                "repository": repo,
                "review_reason": verdict.reason,
                "head_sha": head,
                "last_repo_index": idx,
                "last_repository": repo,
            }
            atomic_json(state_path, result)
            return result
        _record_verified_candidate(
            execution_grant,
            repo,
            base,
            branch,
            head,
            evidence_binding,
            advance_grant_stage_fn,
        )

    if verified_repair and record_verified_repair_fn and promote_verified_repairs_fn:
        record_verified_repair_fn(
            state_path,
            verified_repair["fingerprint"],
            verified_repair["strategy"],
            "canonical exact-head preflight and required candidate review passed",
        )
        promote_verified_repairs_fn(state_path)

    refresh = refresh_base_before_publish_fn(ws, main_branch, base)
    if refresh["status"] == "FETCH_FAILED":
        return _persist(
            state_path,
            {
                "status": "BASE_REFRESH_FAILED",
                "repository": repo,
                "head": head,
                "base_sha": base,
                "last_repo_index": idx,
                "last_repository": repo,
            },
        )
    if refresh["status"] == "CONFLICT":
        return _persist(
            state_path,
            {
                "status": "BASE_RECONCILIATION_CONFLICT",
                "repository": repo,
                "head": head,
                "base_sha": refresh["base_sha"],
                "last_repo_index": idx,
                "last_repository": repo,
            },
        )

    if refresh["status"] == "REBASED":
        base = refresh["base_sha"]
        head = refresh["head_sha"]
        paths = changed_paths_fn(ws, base)
        if not paths:
            return _persist(
                state_path,
                {
                    "status": "BASE_RECONCILIATION_NO_OP",
                    "repository": repo,
                    "head": head,
                    "base_sha": base,
                    "last_repo_index": idx,
                    "last_repository": repo,
                },
            )
        drift_failure = generated_change_failure_fn(
            ws, base, paths, slice_authority
        )
        if drift_failure:
            return _persist(
                state_path,
                {
                    "status": "AUTONOMOUS_CHANGE_REJECTED",
                    "repository": repo,
                    "final_failure": f"post_rebase:{drift_failure}",
                    "head_sha": head,
                    "last_repo_index": idx,
                    "last_repository": repo,
                },
            )

        preflight, verified_head, head_stable = run_exact_head_preflight_fn(
            ws, head
        )
        if preflight.returncode != 0 or not head_stable:
            detail = (
                preflight.stderr
                or preflight.stdout
                or (
                    f"HEAD_CHANGED_DURING_PREFLIGHT:{head}:{verified_head}"
                    if verified_head != head
                    else f"returncode={preflight.returncode}"
                )
            )
            lesson = retain_failure_lesson_fn(
                state_path,
                "post_rebase_preflight",
                detail,
                "PREFLIGHT_FAILED",
            )
            return _persist(
                state_path,
                {
                    "status": "PREFLIGHT_FAILED",
                    "repository": repo,
                    "returncode": preflight.returncode,
                    "verified_head_sha": verified_head,
                    "expected_head_sha": head,
                    "failure_fingerprint": lesson["fingerprint"],
                    "last_repo_index": idx,
                    "last_repository": repo,
                },
            )

        if execution_grant is not None:
            try:
                grant_id = json.loads(
                    execution_grant.read_text(encoding="utf-8")
                )["grant_id"]
                evidence_binding = bind_preflight_evidence_fn(
                    ws, base, head, grant_id
                )
            except (OSError, json.JSONDecodeError, KeyError, TypeError, RuntimeError) as exc:
                return _persist(
                    state_path,
                    {
                        "status": "PREFLIGHT_EVIDENCE_REJECTED",
                        "repository": repo,
                        "review_reason": str(exc),
                        "head_sha": head,
                        "last_repo_index": idx,
                        "last_repository": repo,
                    },
                )

            verdict = review_candidate_fn(
                ws,
                base,
                head,
                slice_authority,
                paths,
                preflight.returncode,
                evidence_binding=evidence_binding,
                grant_id=grant_id,
            )
            if verdict.status != "APPROVE":
                return _persist(
                    state_path,
                    {
                        "status": "INDEPENDENT_REVIEW_REJECTED",
                        "repository": repo,
                        "review_reason": verdict.reason,
                        "head_sha": head,
                        "last_repo_index": idx,
                        "last_repository": repo,
                    },
                )
            _record_verified_candidate(
                execution_grant,
                repo,
                base,
                branch,
                head,
                evidence_binding,
                advance_grant_stage_fn,
            )

    push = run_fn(["git", "push", "-u", "origin", branch], ws)
    if push.returncode:
        return _persist(
            state_path,
            {
                "status": "PUSH_FAILED",
                "repository": repo,
                "head": head,
                "last_repo_index": idx,
                "last_repository": repo,
            },
        )

    if execution_grant is not None:
        advance_grant_stage_fn(
            execution_grant,
            "PUSHED",
            repository=repo,
            base_sha=base,
            branch=branch,
            head_sha=head,
            remote_head_sha=head,
        )

    linear_id = os.environ.get("EXPONENTIAL_LINEAR_ID", "").strip()
    linear_ref = f" Linear: {linear_id}." if linear_id else ""
    body = (
        "Autonomous laptop-actor bounded slice. Local Ollama/Aider produced the change; "
        "canonical exact-head preflight passed. Merge authority remains external and fail-closed."
        + linear_ref
    )
    created = run_fn(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repo,
            "--base",
            main_branch,
            "--head",
            branch,
            "--title",
            "AUTO: bounded autonomous engineering slice",
            "--body",
            body,
        ],
        ws,
    )
    status = "PR_OPEN" if created.returncode == 0 else "PR_CREATE_FAILED"
    pr_url = created.stdout.strip()
    pr_number = (
        pr_number_from_url_fn(pr_url) if created.returncode == 0 else None
    )
    if created.returncode == 0 and pr_number is None:
        status = "PR_IDENTITY_UNPROVEN"
    if execution_grant is not None and status == "PR_OPEN":
        advance_grant_stage_fn(
            execution_grant,
            "PR_OPEN",
            repository=repo,
            base_sha=base,
            branch=branch,
            head_sha=head,
            remote_head_sha=head,
            pr_url=pr_url,
            pr_number=pr_number,
        )

    return _persist(
        state_path,
        {
            "status": status,
            "repository": repo,
            "branch": branch,
            "base_sha": base,
            "head_sha": head,
            "changed_paths": paths,
            "pr_url": pr_url,
            "pr_number": pr_number,
            "last_repo_index": idx,
            "last_repository": repo,
        },
    )


def _record_verified_candidate(
    execution_grant: Path,
    repo: str,
    base: str,
    branch: str,
    head: str,
    evidence_binding: dict[str, Any],
    advance_grant_stage_fn: Callable,
) -> None:
    advance_grant_stage_fn(
        execution_grant,
        "INDEPENDENT_REVIEW_APPROVED",
        repository=repo,
        base_sha=base,
        branch=branch,
        head_sha=head,
        reviewer="deterministic-verdict-v2",
        preflight_evidence_binding=evidence_binding,
    )
    advance_grant_stage_fn(
        execution_grant,
        "COMMITTED",
        repository=repo,
        base_sha=base,
        branch=branch,
        head_sha=head,
    )


def _persist(state_path: Path, result: dict[str, Any]) -> dict[str, Any]:
    atomic_json(state_path, result)
    return result
