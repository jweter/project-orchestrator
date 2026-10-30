"""IG-044 eligibility must never claim a forged local acceptance row."""

from __future__ import annotations

import sqlite3

import pytest

from project_orchestrator.publication_eligibility import (
    PublicationEligibilityError,
    verify_oldest_publication_eligibility,
)
from test_publication_provenance_audit import git, verified


def eligible(case):
    return verify_oldest_publication_eligibility(
        state=case["state"],
        refs=case["refs"],
        repo=case["repo"],
        evidence_path=case["evidence_path"],
        policy=case["policy"],
    )


def test_verified_oldest_is_read_only(verified):
    before = verified["state"].snapshot()
    result = eligible(verified)
    assert result.milestone_id == "m1"
    assert result.accepted_sha == verified["target"]
    assert result.previous_state == "READY"
    assert before == verified["state"].snapshot()


def test_missing_accepted_ref_prevents_eligibility(verified):
    git(verified["repo"], "update-ref", "-d", "refs/iron-god/accepted")
    with pytest.raises(PublicationEligibilityError, match="ACCEPTANCE_NOT_VERIFIED"):
        eligible(verified)


def test_owner_takeover_prevents_eligibility(verified):
    verified["marker"].write_text('{"enabled":true}', encoding="utf8")
    with pytest.raises(PublicationEligibilityError, match="OWNER_CONTROL_VETO"):
        eligible(verified)


def test_prepared_acceptance_blocks_oldest(verified):
    c = verified
    with sqlite3.connect(c["state"].path) as db:
        db.execute(
            "INSERT INTO acceptance_intents VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "pending",
                "jweter/Test",
                c["base"],
                c["target"],
                "f" * 64,
                "NOT_REQUIRED",
                "{}",
                "PREPARED",
                "t",
                "t",
            ),
        )
    assert eligible(c) is None


def test_active_lane_blocks_another_claim(verified):
    c = verified
    with sqlite3.connect(c["state"].path) as db:
        db.execute("UPDATE publications SET state='PUSHING' WHERE milestone_id='m1'")
    assert eligible(c) is None


def test_bad_acceptance_journal_fails_closed(verified):
    c = verified
    with sqlite3.connect(c["state"].path) as db:
        db.execute("DELETE FROM journal WHERE event_type='MILESTONE_ACCEPTED'")
    with pytest.raises(
        PublicationEligibilityError, match="PUBLICATION_JOURNAL_UNTRUSTED"
    ):
        eligible(c)


def test_forged_committed_intent_milestone_and_journal_rejected(verified):
    """An attacker capable of writing SQLite must not establish Git authority."""
    c = verified
    with sqlite3.connect(c["state"].path) as db:
        db.execute(
            "UPDATE publications SET state='RELEASE_SYNCED' WHERE milestone_id='m1'"
        )
        db.execute(
            "INSERT INTO acceptance_intents VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "fake",
                "jweter/Test",
                c["base"],
                c["target"],
                "f" * 64,
                "NOT_REQUIRED",
                "{}",
                "COMMITTED",
                "t",
                "t",
            ),
        )
        db.execute(
            "INSERT INTO milestones VALUES (?,?,?,?,?,?)",
            ("fake", "jweter/Test", c["target"], "f" * 64, "NOT_REQUIRED", "t"),
        )
        db.execute(
            "INSERT INTO publications VALUES (?,?,?,?,?)",
            ("fake", "jweter/Test", "READY", None, "t"),
        )
        db.execute(
            "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) "
            "VALUES (?,?,?,?)",
            ("fake", "MILESTONE_ACCEPTED", c["target"], "t"),
        )
    before = c["state"].snapshot()
    with pytest.raises(PublicationEligibilityError, match="ACCEPTANCE_NOT_VERIFIED"):
        eligible(c)
    assert c["state"].snapshot() == before


def test_receipt_drift_denied(verified):
    c = verified
    c["evidence_path"].write_text(c["evidence_path"].read_text() + "\n")
    with pytest.raises(PublicationEligibilityError, match="ACCEPTANCE_NOT_VERIFIED"):
        eligible(c)


def test_future_schema_requires_migration_review(verified):
    c = verified
    with sqlite3.connect(c["state"].path) as db:
        db.execute("PRAGMA user_version=3")
    with pytest.raises(
        PublicationEligibilityError, match="UNSUPPORTED_INSPECTION_SCHEMA"
    ):
        eligible(c)
