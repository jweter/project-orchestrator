"""Review repair must preserve owner path restrictions and add regression coverage."""
from __future__ import annotations

from scripts import iron_god_product_worker as worker


def setup(tmp_path):
    module = tmp_path / "pkg" / "helper.py"
    test = tmp_path / "tests" / "test_helper.py"
    module.parent.mkdir()
    test.parent.mkdir()
    module.write_text("def f(x):\n    return x\n", encoding="utf-8")
    test.write_text("from pkg.helper import f\n\ndef test_original():\n    assert f(1) == 1\n", encoding="utf-8")
    packet = {"paths": ["pkg", "tests/test_helper.py"],
              "planning": {"forbidden_paths": []},
              "task": "Repair the review finding", "packet_id": "p1"}
    repair = {"module_path": "pkg/helper.py", "test_path": "tests/test_helper.py",
              "findings": [{"body": "P2 fix malformed inputs", "path": "pkg/helper.py"}],
              "pr": {"number": 422}}
    return module, test, packet, repair


def run(tmp_path, monkeypatch, agent, *, packet=None):
    module, test, default_packet, repair = setup(tmp_path)
    packet = packet or default_packet
    monkeypatch.setattr(worker, "transcript_tail", lambda *args: "")
    monkeypatch.setattr(worker, "changed_paths", lambda *args: [
        path for path, changed in [("pkg/helper.py", module.read_text() != "def f(x):\n    return x\n"),
                                  ("tests/test_helper.py", test.read_text() !=
                                   "from pkg.helper import f\n\ndef test_original():\n    assert f(1) == 1\n")]
        if changed])
    monkeypatch.setattr(worker, "slice_checks", lambda *args: {"ok": True})
    commits = []
    def commit(*args, **kwargs):
        commits.append((args[3], args[4]))
        return {"status": "PASS", "detail": "mocked only"}
    monkeypatch.setattr(worker, "commit_and_verify", commit)
    outcome = worker.execute_review_repair(
        packet, repair, tmp_path, "a" * 40, {}, lambda status, detail: {"status": status, "detail": detail},
        authority=lambda: True, check=lambda: None, run_agent=agent,
        gates=lambda *args: None, preflight=lambda *args: {"ok": True},
        format_files=lambda *args: "")
    return outcome, commits, module, test


def test_repair_never_launches_model_for_test_outside_owner_scope(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("must not call model")
    packet = {"paths": ["pkg"], "planning": {"forbidden_paths": []},
              "task": "Review", "packet_id": "p1"}
    outcome, commits, _, _ = run(tmp_path, monkeypatch, forbidden, packet=packet)
    assert outcome["status"] == "REVIEW_REQUIRED"
    assert "TEST_OUT_OF_APPROVED_SCOPE" in outcome["detail"]
    assert not commits


def test_repair_does_not_accept_edited_existing_test(tmp_path, monkeypatch):
    def change(worktree, message, paths, check):
        assert paths == ["pkg/helper.py", "tests/test_helper.py"]
        (worktree / "pkg/helper.py").write_text("def f(x):\n    return str(x)\n")
        (worktree / "tests/test_helper.py").write_text("def test_deleted():\n    assert True\n")
    outcome, commits, _, _ = run(tmp_path, monkeypatch, change)
    assert outcome["status"] == "REVIEW_REQUIRED"
    assert outcome["detail"] == "REVIEW_REPAIR_EXISTING_TESTS_CHANGED"
    assert not commits


def test_repair_requires_new_regression_test(tmp_path, monkeypatch):
    def change(worktree, message, paths, check):
        (worktree / "pkg/helper.py").write_text("def f(x):\n    return str(x)\n")
    outcome, commits, _, _ = run(tmp_path, monkeypatch, change)
    assert outcome["detail"] == "REVIEW_REPAIR_MISSING_ADDITIVE_REGRESSION_TEST"
    assert not commits


def test_repair_can_append_test_and_use_normal_verification_gate(tmp_path, monkeypatch):
    def change(worktree, message, paths, check):
        assert "untrusted context" in message
        (worktree / "pkg/helper.py").write_text("def f(x):\n    return str(x)\n")
        with (worktree / "tests/test_helper.py").open("a", encoding="utf-8") as handle:
            handle.write("\ndef test_new_behavior():\n    assert f(2) == '2'\n")
    outcome, commits, _, _ = run(tmp_path, monkeypatch, change)
    assert outcome["status"] == "PASS"
    assert commits == [(["pkg/helper.py", "tests/test_helper.py"],
                        ["pkg/helper.py", "tests/test_helper.py"])]
