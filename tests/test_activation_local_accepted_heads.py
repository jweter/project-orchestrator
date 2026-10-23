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


@pytest.mark.parametrize('heads', [{}, {'repository': 'main'}, {'repository': 'g' * 40}])
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
