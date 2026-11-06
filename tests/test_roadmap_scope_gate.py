"""The GitHub-side scope gate: authorization only, report-only unless enforcing, never trusts the pull request itself."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from project_orchestrator import iron_god_portfolio_merger as pm
from tests.test_iron_god_portfolio_merger import REPO, TITLE, FakePort, file_row

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "roadmap_scope_gate.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("roadmap_scope_gate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GatePort(FakePort):
    def pull_summary(self, repo: str, number: int) -> dict:
        return dict(self.pulls[repo][0])

    def threads(self, repo, number):  # the gate must never read review conversations or timing
        raise AssertionError("the scope gate does not judge review threads")

    def quiet_since(self, repo, number, head, created_at):
        raise AssertionError("the scope gate does not judge timing")


def run(port: GatePort, *args: str) -> int:
    return load_gate().main(["--repo", REPO, "--pr", "7", *args], port=port)


def test_a_change_inside_the_authorized_scope_passes_in_both_modes(capsys) -> None:
    port = GatePort()
    assert run(port) == 0 and run(port, "--enforce") == 0
    out = capsys.readouterr().out
    assert "AUTHORIZED" in out and "cap-cache/s1" in out and "::error" not in out and "::warning" not in out


def test_a_change_outside_the_scope_is_reported_only_until_the_gate_is_enforced(capsys) -> None:
    port = GatePort(files_rows=[file_row("pkg/cache.py"), file_row("docs/elsewhere.md")])
    assert run(port) == 0
    assert "::warning::ESCALATE (report-only)" in capsys.readouterr().out
    assert run(port, "--enforce") == 1
    out = capsys.readouterr().out
    assert "::error::ESCALATE" in out and "OUT_OF_SCOPE_PATHS:docs/elsewhere.md" in out and "needs the owner" in out


@pytest.mark.parametrize("files, fragment", [
    ([file_row("pkg/cache.py"), file_row(".github/workflows/ci.yml")], "SECURITY_SENSITIVE_PATH"),
    ([file_row("pkg/cache.py"), file_row("pyproject.toml")], "DEPENDENCY_CHANGE_NEEDS_OWNER"),
    ([file_row("pkg/cache.py", patch='+token = "ghp_' + "a" * 36 + '"\n')], "SECRET_PATTERN_IN_DIFF"),
])
def test_the_gate_applies_the_same_diff_policy_as_the_merger(capsys, files, fragment) -> None:
    assert run(GatePort(files_rows=files), "--enforce") == 1
    assert fragment in capsys.readouterr().out


def test_a_pull_request_that_is_not_roadmap_development_is_not_applicable(capsys) -> None:
    port = GatePort()
    port.pulls[REPO] = [FakePort.summary(title="Fix a typo")]
    assert run(port, "--enforce") == 0
    assert "IGNORE" in capsys.readouterr().out


def test_ci_state_is_not_the_gates_business_so_failing_checks_do_not_change_its_verdict() -> None:
    port = GatePort()
    port.pulls[REPO] = [FakePort.summary(statusCheckRollup=[{"name": "Canonical preflight", "status": "COMPLETED",
                                                           "conclusion": "FAILURE"}], mergeStateStatus="BLOCKED")]
    assert run(port, "--enforce") == 0  # GitHub's required checks block the merge; the gate only judges scope


def test_the_pull_request_number_comes_from_the_event_and_the_summary_file_is_written(tmp_path: Path, monkeypatch) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}}), encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert load_gate().main(["--repo", REPO], port=GatePort()) == 0
    assert "Roadmap scope gate: AUTHORIZED" in summary.read_text(encoding="utf-8")
    monkeypatch.delenv("GITHUB_EVENT_PATH")
    with pytest.raises(SystemExit):
        load_gate().main(["--repo", REPO], port=GatePort())


def test_scope_verdict_and_evaluate_share_one_authorization() -> None:
    facts = {**__import__("tests.test_iron_god_portfolio_merger", fromlist=["facts"]).facts(), "files": [file_row("docs/x.md")]}
    assert pm.scope_verdict(facts).action == pm.ESCALATE
    assert pm.evaluate(facts, now=__import__("tests.test_iron_god_portfolio_merger", fromlist=["NOW"]).NOW).action == pm.ESCALATE
    assert TITLE.startswith("Iron God [RM-")


def test_a_dependency_bot_pull_request_is_not_applicable_to_the_gate_but_the_merger_still_escalates_it(capsys) -> None:
    port = GatePort()
    port.pulls[REPO] = [FakePort.summary(title="build(deps-dev): bump pytest", author={"login": "app/dependabot"})]
    assert run(port, "--enforce") == 0          # a required check must never block the owner from merging a bot PR
    assert "NOT_ROADMAP_PR" in capsys.readouterr().out
    facts = pm.gather(port, REPO, port.pulls[REPO][0], scope_only=True)
    assert pm.scope_verdict(facts).action == pm.IGNORE
    assert pm.evaluate(facts, now=__import__("tests.test_iron_god_portfolio_merger", fromlist=["NOW"]).NOW).action == pm.ESCALATE
