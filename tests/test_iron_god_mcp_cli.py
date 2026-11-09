"""The CLI adapter is what ChatGPT uses through its existing bridge: advisory-only, read-only by default, one database."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path

import pytest

from project_orchestrator import iron_god_mcp_cli as cli
from project_orchestrator import mcp_coordination as coord

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/project_orchestrator/iron_god_mcp_cli.py"


@pytest.fixture
def world(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "repo").mkdir()
    return {"db": tmp_path / "coordination.sqlite", "state": tmp_path / "state", "repo": tmp_path / "repo"}


def call(world, label, *argv, writes=False, stdin=""):
    env = {"IRON_GOD_MCP_COORDINATION_DB": str(world["db"]), "IRON_GOD_MCP_STATE_ROOT": str(world["state"]),
           "IRON_GOD_MCP_REPO_ROOT": str(world["repo"]), "IRON_GOD_MCP_CLIENT_LABEL": label}
    if writes:
        env["IRON_GOD_MCP_ALLOW_ADVISORY_WRITES"] = "1"
    out = io.StringIO()
    code = cli.run(list(argv), env=env, stdin=io.StringIO(stdin), out=out, sleep=lambda s: None)
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def test_the_default_is_read_only_posting_and_acking_are_refused(world):
    code, result = call(world, "claude-code", "post", "--to", "chatgpt", "--kind", "handoff", "--body", "hello")
    assert code == 1 and result["status"] == "READ_ONLY"
    code, result = call(world, "chatgpt", "ack", "--id", "a" * 32)
    assert code == 1 and result["status"] == "READ_ONLY"
    assert call(world, "chatgpt", "read")[1]["messages"] == []                      # nothing was written


def test_three_participants_exchange_a_handoff_without_a_human_relay(world):
    code, posted = call(world, "claude-code", "post", "--to", "chatgpt", "--kind", "handoff", "--reference", "PR#668",
                        "--body", "Dashboard merged; please review the release plan.", writes=True)
    assert code == 0 and posted["status"] == "OK"
    code, mailbox = call(world, "chatgpt", "read")
    assert code == 0 and [m["body"] for m in mailbox["messages"]] == ["Dashboard merged; please review the release plan."]
    message = mailbox["messages"][0]
    assert message["sender_label"] == "claude-code" and message["reference"] == "PR#668" and not message["acknowledged"]
    assert call(world, "iron-god", "read")[1]["messages"] == []                       # not addressed to Iron God
    assert call(world, "chatgpt", "status")[1]["unacknowledged"] == 1
    code, receipt = call(world, "chatgpt", "ack", "--id", message["message_id"], writes=True)
    assert code == 0 and receipt["status"] == "OK"
    assert call(world, "chatgpt", "status")[1]["unacknowledged"] == 0
    call(world, "chatgpt", "post", "--to", "iron-god", "--kind", "notification", "--body-stdin", writes=True,
         stdin="Reviewed; proceed with the authorized release workflow.")
    assert [m["sender_label"] for m in call(world, "iron-god", "read")[1]["messages"]] == ["chatgpt"]
    assert call(world, "claude-code", "read")[1]["messages"] == []                    # claude-code was not addressed


def test_posting_the_same_handoff_twice_is_idempotent_and_a_repeat_reads_from_a_cursor(world):
    args = ("post", "--to", "all", "--kind", "status", "--body", "same text")
    call(world, "operator", *args, writes=True)
    call(world, "operator", *args, writes=True)
    messages = call(world, "chatgpt", "read")[1]["messages"]
    assert len(messages) == 1
    assert call(world, "chatgpt", "read", "--after", str(messages[0]["seq"]))[1]["messages"] == []


def test_bad_input_is_refused_not_raised(world):
    assert call(world, "chatgpt", "post", "--to", "nobody", "--kind", "handoff", "--body", "x", writes=True)[0] == 1
    assert call(world, "chatgpt", "post", "--to", "all", "--kind", "execute", "--body", "x", writes=True)[0] == 1
    assert call(world, "chatgpt", "post", "--to", "all", "--kind", "status", "--body", "x" * 4000, writes=True)[0] == 1
    secret = call(world, "chatgpt", "post", "--to", "all", "--kind", "status", "--body", "token ghp_abcdefghijklmnop1234", writes=True)
    assert secret[0] == 1                                                              # the store's own secret filter still applies
    assert call(world, "someone-else", "read")[0] == 1                                 # an unknown participant label


def test_missing_configuration_is_reported_and_nothing_is_created(world):
    out = io.StringIO()
    assert cli.run(["read"], env={}, out=out) == 2 and "CONFIGURATION_REQUIRED" in out.getvalue()
    assert not world["db"].exists()


def test_read_wait_polls_for_a_bounded_time_then_returns(world):
    ticks = {"t": 0.0}
    slept = []

    def clock():
        return ticks["t"]

    def sleep(seconds):
        slept.append(seconds)
        ticks["t"] += seconds
        if len(slept) == 2:                                                            # a message arrives while polling
            coord.post_message(world["db"], sender_label="claude-code", recipient="chatgpt", kind="status", body="late",
                               idempotency_key="k1")

    coord.initialize(world["db"])
    env = {"IRON_GOD_MCP_COORDINATION_DB": str(world["db"]), "IRON_GOD_MCP_STATE_ROOT": str(world["state"]),
           "IRON_GOD_MCP_REPO_ROOT": str(world["repo"]), "IRON_GOD_MCP_CLIENT_LABEL": "chatgpt"}
    out = io.StringIO()
    assert cli.run(["read", "--wait", "30"], env=env, out=out, sleep=sleep, clock=clock) == 0
    assert [m["body"] for m in json.loads(out.getvalue())["messages"]] == ["late"] and len(slept) == 2
    empty = io.StringIO()
    ticks["t"], slept[:] = 0.0, []
    assert cli.run(["read", "--after", "999", "--wait", "6"], env=env, out=empty, sleep=sleep, clock=clock) == 0
    assert json.loads(empty.getvalue())["messages"] == [] and len(slept) == 3          # gave up at the deadline, no infinite wait


def test_the_adapter_has_no_shell_network_or_control_surface():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    assert not imported & {"subprocess", "socket", "urllib", "http", "requests", "asyncio", "ssl", "multiprocessing", "ctypes", "shutil"}
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"system", "popen", "startfile", "run", "Popen", "kill", "unlink", "rmtree", "write_text", "write_bytes"}
    source = MODULE.read_text(encoding="utf-8").lower()
    for forbidden in ("owner_control", "take_the_wheel", "give me control", "merge", "dispatch", "promote", "autopilot", "aura"):
        assert forbidden not in source.split('"""', 2)[2], forbidden                    # no such operation exists in the code


def test_a_huge_wait_is_capped_so_an_agent_can_never_block_forever(world):
    coord.initialize(world["db"])
    env = {"IRON_GOD_MCP_COORDINATION_DB": str(world["db"]), "IRON_GOD_MCP_STATE_ROOT": str(world["state"]),
           "IRON_GOD_MCP_REPO_ROOT": str(world["repo"]), "IRON_GOD_MCP_CLIENT_LABEL": "chatgpt"}
    ticks = {"t": 0.0}
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        ticks["t"] += seconds
        assert len(slept) <= 1000, "the wait is not capped"          # fail fast instead of looping for hours

    assert cli.run(["read", "--wait", "99999999"], env=env, out=io.StringIO(), sleep=sleep, clock=lambda: ticks["t"]) == 0
    assert len(slept) == int(cli.MAX_WAIT_S / cli.POLL_S)                                # gave up at the 300 s ceiling


# --- review findings on #669: each of these failed before its fix ---------------------------------------------------------
def test_a_status_read_never_creates_or_initializes_the_database(world):
    code, result = call(world, "chatgpt", "status")
    assert code == 0 and result["unacknowledged"] == 0 and result["database_initialized"] is False
    assert not world["db"].exists()                                                    # read-only really means read-only


def test_status_counts_unacknowledged_messages_beyond_the_first_page(world):
    coord.initialize(world["db"])
    for n in range(coord.MAX_PAGE + 5):
        coord.post_message(world["db"], sender_label="claude-code", recipient="chatgpt", kind="status", body=f"m{n}",
                           idempotency_key=f"k{n}")
    first_page = coord.list_messages(world["db"], recipient_label="chatgpt", limit=coord.MAX_PAGE)["messages"]
    for m in first_page:                                                               # the oldest 100 are all acknowledged
        coord.ack_message(world["db"], recipient_label="chatgpt", message_id=m["message_id"])
    code, result = call(world, "chatgpt", "status")
    assert code == 0 and result["unacknowledged"] == 5 and result["database_initialized"] is True   # the newer 5 still wait


def test_a_corrupt_database_is_reported_as_json_not_a_traceback(world):
    world["db"].write_bytes(b"this is not a sqlite database at all" * 40)
    for argv in (("read",), ("status",)):
        code, result = call(world, "chatgpt", *argv)
        assert code == 1 and result["status"] == "REFUSED" and result["detail"]
    code, result = call(world, "claude-code", "post", "--to", "chatgpt", "--kind", "status", "--body", "x", writes=True)
    assert code == 1 and result["status"] == "REFUSED"


def test_status_includes_read_only_delivery_metrics_for_each_participant(world):
    code, empty = call(world, "chatgpt", "status")
    assert code == 0 and empty["delivery"]["status"] == "UNINITIALIZED"
    assert not world["db"].exists()
    code, sent = call(world, "claude-code", "post", "--to", "chatgpt",
                      "--kind", "question", "--body", "Please review PR#675", writes=True)
    assert code == 0
    code, status = call(world, "chatgpt", "status")
    assert code == 0 and status["unacknowledged"] == 1
    assert status["delivery"]["participants"]["chatgpt"]["unacknowledged"] == 1
    assert status["delivery"]["participants"]["claude-code"]["unacknowledged"] == 0
    assert "Please review PR#675" not in json.dumps(status["delivery"])
    call(world, "chatgpt", "ack", "--id", sent["message_id"], writes=True)
    assert call(world, "chatgpt", "status")[1]["delivery"]["participants"]["chatgpt"]["acknowledged"] == 1
