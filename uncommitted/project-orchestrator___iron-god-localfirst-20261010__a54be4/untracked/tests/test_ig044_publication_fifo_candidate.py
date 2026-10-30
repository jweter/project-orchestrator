"""Focused regression tests for the standalone IG-044 FIFO candidate."""
import importlib.util
import sqlite3
from pathlib import Path

from project_orchestrator.local_first_state import LocalFirstState

SPEC = importlib.util.spec_from_file_location(
    "ig044_publication_fifo_candidate",
    Path(__file__).resolve().parents[1] / "engineering" / "ig044_publication_fifo_candidate.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
claim = module.claim_oldest_publication
REPO = "jweter/VoiceStudio"


def setup(tmp_path):
    store = LocalFirstState(tmp_path / "iron.sqlite")
    for ident, sha in (("first", "a" * 40), ("second", "b" * 40)):
        store.record_accepted_milestone(
            repository=REPO, milestone_id=ident, accepted_sha=sha,
            verification_sha256="f" * 64, product_reality="PASSED",
        )
    return store


def test_claim_oldest_then_block_second(tmp_path):
    store = setup(tmp_path)
    first = claim(store.path, REPO)
    assert first["milestone_id"] == "first"
    assert first["state"] == "PUSHING"
    assert claim(store.path, REPO) is None


def test_deferred_oldest_reclaims_before_newer(tmp_path):
    store = setup(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE publications SET state='DEFERRED' WHERE milestone_id='first'")
    assert claim(store.path, REPO)["milestone_id"] == "first"


def test_corrupt_acceptance_journal_fails_closed(tmp_path):
    store = setup(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE journal SET detail=? WHERE event_type='MILESTONE_ACCEPTED' AND milestone_id='first'", ("0" * 40,))
    assert claim(store.path, REPO) is None
    assert store.snapshot()["publications"][0]["state"] == "READY"


def test_prepared_acceptance_blocks_claim(tmp_path):
    store = setup(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "INSERT INTO acceptance_intents VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("pending", REPO, "c"*40, "d"*40, "f"*64, "PASSED", "{}", "PREPARED", "2026-01-01", "2026-01-01"),
        )
    assert claim(store.path, REPO) is None


def test_invalid_repository_rejected(tmp_path):
    store = setup(tmp_path)
    try:
        claim(store.path, "invalid")
    except module.PublicationClaimError as exc:
        assert str(exc) == "INVALID_REPOSITORY"
    else:
        raise AssertionError("invalid repository accepted")
