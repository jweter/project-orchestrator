"""Verified offload receipt references: never confuse evidence with authority."""
from __future__ import annotations

import sqlite3
import subprocess

import pytest

from project_orchestrator import iron_god_offload as offload
from project_orchestrator import iron_god_offload_receipts as receipts

REPO = "jweter/VoiceStudio"
DIGEST = "a" * 64


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True,
    ).stdout.decode().strip()


@pytest.fixture
def case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "src").mkdir()
    src = repo / "src" / "unit.py"
    src.write_text("def unit():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "src/unit.py")
    git(repo, "-c", "user.name=Canary", "-c", "user.email=canary@localhost",
        "commit", "-q", "-m", "baseline")
    base = git(repo, "rev-parse", "HEAD")
    db = tmp_path / "sqlite" / "coordination.sqlite"
    db.parent.mkdir()
    offer = offload.offer_slice(
        db, repository=REPO, slice_id="VS-EVIDENCE-1", base_sha=base,
        allowed_paths=["src/unit.py"], authority_ref="issue:747",
        failure_ref="packet:VS-P-1",
    )
    offload.claim_slice(db, offer["offer_id"])
    src.write_text("def unit():\n    return 2\n", encoding="utf-8")
    patch = subprocess.run(
        ["git", "-C", str(repo), "diff", "HEAD", "--", "src/unit.py"],
        capture_output=True, check=True,
    ).stdout
    git(repo, "restore", "--", "src/unit.py")
    stage = tmp_path / "staging"
    candidate = offload.seal_candidate(offer, patch, stage)
    offload.register_candidate(db, candidate)
    return db, offer, candidate, stage, base


def params(case, **changes):
    db, offer, candidate, stage, base = case
    args = {
        "offer_id": offer["offer_id"], "candidate": candidate, "stage_root": stage,
        "gate": "TARGETED_TESTS", "result": "PASS",
        "tested_head": base, "evidence_ref": "native:job-17",
        "evidence_sha256": DIGEST,
        "authenticated_owner_check": lambda: True,
        "active_lease_check": lambda: True,
        "independent_evidence_check": lambda _: True,
    }
    args.update(changes)
    return db, args


def test_rejected_without_all_independent_gates(case):
    db, data = params(case)
    for field in ("authenticated_owner_check", "active_lease_check",
                  "independent_evidence_check"):
        with pytest.raises(offload.OffloadRejected, match="GATES_REQUIRED"):
            receipts.record_independently_checked_evidence(db, **{**data, field: None})
    assert receipts.read_verification_references(
        db, offer_id=data["offer_id"],
    )["items"] == []


@pytest.mark.parametrize("field", [
    "authenticated_owner_check", "active_lease_check", "independent_evidence_check",
])
def test_negative_authentication_or_independent_evidence_refuses_write(case, field):
    db, data = params(case)
    data[field] = lambda *_: False
    with pytest.raises(offload.OffloadRejected, match="EVIDENCE_DENIED"):
        receipts.record_independently_checked_evidence(db, **data)
    assert receipts.read_verification_references(
        db, offer_id=data["offer_id"],
    )["items"] == []


def test_idempotent_append_only_reference_and_advisory_read(case):
    db, data = params(case)
    first = receipts.record_independently_checked_evidence(db, **data)
    assert first["status"] == "EXTERNALLY_CHECKED_EVIDENCE_RECORDED"
    assert not first["replayed"]
    assert first["execution_authorized"] is False
    assert first["merge_authorized"] is False
    assert first["skill_promoted"] is False
    replay = receipts.record_independently_checked_evidence(db, **data)
    assert replay["replayed"] and replay["receipt_id"] == first["receipt_id"]
    view = receipts.read_verification_references(db, offer_id=data["offer_id"])
    assert view["authority"] == "ADVISORY_ONLY"
    assert view["learning_promotion_authorized"] is False
    assert len(view["items"]) == 1
    assert view["items"][0]["gate"] == "TARGETED_TESTS"
    assert view["items"][0]["evidence_sha256"] == DIGEST
    assert "candidate" not in str(view)


def test_conflicting_same_evidence_ref_fails_closed(case):
    db, data = params(case)
    receipts.record_independently_checked_evidence(db, **data)
    with pytest.raises(offload.OffloadRejected, match="CONFLICTING_VERIFICATION"):
        receipts.record_independently_checked_evidence(db, **{
            **data, "result": "FAIL",
        })
    assert len(receipts.read_verification_references(
        db, offer_id=data["offer_id"],
    )["items"]) == 1


def test_revocation_immediately_before_db_commit_fails_closed(case):
    db, data = params(case)
    probes = [True, False]
    data["authenticated_owner_check"] = lambda: probes.pop(0)
    with pytest.raises(offload.OffloadRejected, match="VERIFICATION_AUTHORITY_REVOKED"):
        receipts.record_independently_checked_evidence(db, **data)
    assert receipts.read_verification_references(
        db, offer_id=data["offer_id"],
    )["items"] == []


@pytest.mark.parametrize("changes", [
    {"gate": "OWNER_GRANT"}, {"result": "SUCCESS"},
    {"tested_head": "bad"}, {"evidence_sha256": "not-a-digest"},
    {"evidence_ref": "C:\\Users\\Private\\test.log"},
    {"evidence_ref": "native:../../secret"},
    {"evidence_ref": "native:/absolute/path"},
    {"evidence_ref": "native:test//artifact"},
    {"evidence_ref": "native:C:/Windows/System32"},
    {"evidence_ref": "github:https://example.invalid/path"},
    {"gate": None}, {"gate": []}, {"result": []},
])
def test_shape_and_raw_private_path_inputs_refused(case, changes):
    db, data = params(case, **changes)
    with pytest.raises(offload.OffloadRejected, match="SHAPE_INVALID"):
        receipts.record_independently_checked_evidence(db, **data)


def test_no_self_reported_success_after_patch_bytes_tampered(case):
    db, data = params(case)
    candidate = data["candidate"]
    from pathlib import Path
    manifest = Path(candidate["manifest_path"])
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(offload.OffloadRejected, match="MANIFEST_HASH_MISMATCH"):
        receipts.record_independently_checked_evidence(db, **data)


def test_independent_receipts_do_not_auto_promote_learning_or_merge(case):
    db, data = params(case)
    for gate, ref in (
        ("TARGETED_TESTS", "native:test-1"),
        ("FULL_PREFLIGHT", "preflight:run-2"),
        ("GITHUB_CI", "github:run-3"),
        ("MERGE", "github:merge-4"),
        ("PRODUCT_REALITY", "product:session-5"),
    ):
        result = receipts.record_independently_checked_evidence(
            db, **{**data, "gate": gate, "evidence_ref": ref},
        )
        assert result["merge_authorized"] is False
        assert result["skill_promoted"] is False
    view = receipts.read_verification_references(db, offer_id=data["offer_id"])
    assert len(view["items"]) == 5
    assert not view["learning_promotion_authorized"]


def test_sqlite_receipts_are_immutable(case):
    db, data = params(case)
    out = receipts.record_independently_checked_evidence(db, **data)
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE_OFFLOAD_VERIFICATION"):
            conn.execute(
                "UPDATE offload_verification_receipts SET gate='MERGE' WHERE receipt_id=?",
                (out["receipt_id"],),
            )
        with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE_OFFLOAD_VERIFICATION"):
            conn.execute(
                "DELETE FROM offload_verification_receipts WHERE receipt_id=?",
                (out["receipt_id"],),
            )


def test_bounded_read_only_view_rejects_invalid_limit(case):
    db, data = params(case)
    for limit in (0, -1, 31, True, 1.5):
        with pytest.raises(offload.OffloadRejected, match="INVALID_VERIFICATION_PAGE_SIZE"):
            receipts.read_verification_references(db, offer_id=data["offer_id"], limit=limit)


def test_mcp_read_only_receipts_are_visible_across_server_recreation(case, monkeypatch):
    """Existing verified reference is observable without MCP execution/writes."""
    import sys
    import types

    from project_orchestrator import iron_god_mcp_server as gateway

    db, data = params(case)
    recorded = receipts.record_independently_checked_evidence(db, **data)
    assert recorded["status"] == "EXTERNALLY_CHECKED_EVIDENCE_RECORDED"

    class LocalMCP:
        def __init__(self, _name):
            self.tools = {}

        def tool(self):
            def register(func):
                self.tools[func.__name__] = func
                return func
            return register

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    server_module.MCPServer = LocalMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)

    for _ in range(2):  # no new session state needed on restart
        server = gateway.create_server(
            database=db, repo_root=db.parent, state_root=db.parent,
            client_label="chatgpt", allow_advisory_writes=False,
        )
        assert "post_message" not in server.tools
        assert "ack_message" not in server.tools
        assert "stage_fetched_github_candidate" not in server.tools
        view = server.tools["get_offload_verification_references"](
            data["offer_id"], limit=1,
        )
        assert view["authority"] == "ADVISORY_ONLY"
        assert view["execution_authorized"] is False
        assert view["merge_authorized"] is False
        assert view["learning_promotion_authorized"] is False
        assert len(view["items"]) == 1
        assert view["items"][0]["receipt_id"] == recorded["receipt_id"]
        assert "manifest_path" not in str(view)
        assert "candidate" not in str(view)

    with sqlite3.connect(db) as connection:
        count = connection.execute(
            "SELECT count(*) FROM offload_verification_receipts",
        ).fetchone()[0]
    assert count == 1
