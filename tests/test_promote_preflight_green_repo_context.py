"""Regression checks for checkout-free exact-head preflight promotion (#741)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-preflight-green.yml"


def test_checkout_free_job_supplies_repository_context_for_all_gh_steps():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    job = workflow.split("\n  verify-promotion-gate:\n", 1)[1]
    assert "actions/checkout@" not in job
    # GitHub CLI discovers repos from a Git checkout *unless* GH_REPO is
    # provided. The common job env applies to download and all PR commands.
    assert "\n    env:\n      GH_REPO: ${{ github.repository }}\n    steps:\n" in job
    assert 'gh run download "$RUN_ID" --name preflight-evidence --dir .preflight' in job
    assert 'gh pr list --head "$BRANCH"' in job
    assert 'gh pr view "$PR_NUMBER"' in job
    assert 'gh pr ready "$PR_NUMBER"' in job


def test_repo_context_fix_preserves_exact_head_fail_closed_checks():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for required in (
        "github.event.workflow_run.conclusion == 'success'",
        'github.event.workflow_run.head_sha',
        'git ls-remote "https://github.com/$REPOSITORY.git"',
        'if [ -z "$CURRENT_HEAD" ] || [ "$CURRENT_HEAD" != "$EXPECTED_HEAD_SHA" ]; then',
        'if evidence.get("repository") != expected_repository:',
        'if evidence.get("head_sha") != expected_head:',
        'if evidence.get("mode") != "FULL":',
        'if evidence.get("status") != "GREEN":',
    ):
        assert required in workflow


def test_deleted_branch_terminal_path_cannot_promote_draft():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in workflow
    assert '.head.repo.full_name == $repo and .base.repo.full_name == $repo' in workflow
    assert '.head.ref == $branch and .head.sha == $sha' in workflow
    assert '.merged_at != null and .state == "closed"' in workflow
    assert 'if [ "$MERGED" = "1" ]; then' in workflow
    assert "if: steps.branch_gate.outputs.already_merged != 'true'" in workflow
    assert 'if [ "$PR_HEAD" != "$EXPECTED_HEAD_SHA" ]; then' in workflow
