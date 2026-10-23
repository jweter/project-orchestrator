from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator.activation_capability import _readiness_worktrees


def test_explicit_local_head_is_used(tmp_path: Path):
    repo = tmp_path / 'repository'
    repo.mkdir()
    calls = []
    sha = 'a' * 40

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='commit\n')

    audited, root = _readiness_worktrees([repo], tmp_path / 'state', run, accepted_heads={'repository': sha})
    assert calls[0] == ['git', 'cat-file', '-t', sha]
    assert calls[1][-1] == sha
    assert audited == [root / 'repository']


@pytest.mark.parametrize('heads', [{'repository': 'main'}, {'repository': 'g' * 40}])
def test_invalid_accepted_head_fails_closed(tmp_path: Path, heads):
    repo = tmp_path / 'repository'
    repo.mkdir()
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='commit\n')

    with pytest.raises(RuntimeError, match='accepted head missing or invalid'):
        _readiness_worktrees([repo], tmp_path / 'state', run, accepted_heads=heads)
    assert not any(call[:3] == ['git', 'worktree', 'add'] for call in calls)


def test_unavailable_local_commit_fails_closed(tmp_path: Path):
    repo = tmp_path / 'repository'
    repo.mkdir()

    def run(args, **kwargs):
        return SimpleNamespace(returncode=1, stdout='')

    with pytest.raises(RuntimeError, match='accepted commit unavailable'):
        _readiness_worktrees([repo], tmp_path / 'state', run, accepted_heads={'repository': 'a' * 40})


@pytest.mark.parametrize('heads', [
    {},
    {'repository': 'a' * 40, 'unrelated': 'b' * 40},
])
def test_accepted_map_must_match_all_repositories(tmp_path: Path, heads):
    repo = tmp_path / 'repository'
    repo.mkdir()

    def run(*args, **kwargs):
        pytest.fail('No Git operation should run for an incomplete acceptance map')

    with pytest.raises(RuntimeError, match='accepted heads must exactly match'):
        _readiness_worktrees([repo], tmp_path / 'state', run, accepted_heads=heads)
    assert not (tmp_path / 'state').exists()


def test_duplicate_repository_names_fail_before_git_or_workspace_creation(tmp_path: Path):
    repos = [tmp_path / 'one' / 'shared', tmp_path / 'two' / 'shared']
    for repo in repos:
        repo.mkdir(parents=True)

    def run(*args, **kwargs):
        pytest.fail('Ambiguous repositories must not be accessed')

    with pytest.raises(RuntimeError, match='repository names are ambiguous'):
        _readiness_worktrees(repos, tmp_path / 'state', run)
    assert not (tmp_path / 'state').exists()
