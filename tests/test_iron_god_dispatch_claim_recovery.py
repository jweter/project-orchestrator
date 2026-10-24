"""Durable claim-recovery canary over real local Git and disposable SQLite.

Owner directive (2026-10-18, "ENGINE RECOVERY AND AUTHORIZATION
RESOLUTION"): a claim that is conclusively proven to have never reached a
worker may be given a terminal ABORTED_NON_EXECUTED disposition, and only
then may a new, independently fenced attempt be claimed for the same
acceptance event and slice -- without deleting, overwriting, or replaying
the original reservation, and without manufacturing a new acceptance
milestone. These tests prove each required case (A-I from the directive)
against real, disposable Git repositories and SQLite databases -- never
mocked storage.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3

import pytest

from project_orchestrator.iron_god_candidate_refs import set_candidate
from project_orchestrator.local_dispatch_event_claim import (
    DispatchFenceError,
    _connect,
    _initialize,
    assess_non_execution_evidence,
    claim_accepted_next,
    inspect_claims,
    invoke_once,
    recover_non_executed_claim,
)
from test_local_dispatch_event_claim import accept_feature_a, demo, git, reserve, store
from test_local_dispatch_outcome_reconciliation import (
    build_store,
    put_lease,
    put_outcome,
    worker_store,
)

AUTHORIZED_BY = "jeremyweter@gmail.com"


def recover(demo, claim, **overrides):
    args = dict(
        claim=claim,
        state=demo[3],
        refs=demo[2],
        owner_state_root=demo[1],
        claims_path=store(demo),
        worker_db_path=worker_store(demo),
        repo_root=demo[0],
        authorized_by=AUTHORIZED_BY,
        reason="second commissioning attempt never reached a worker",
    )
    args.update(overrides)
    return recover_non_executed_claim(**args)


def reserve_to_invocation_reserved(demo):
    """Drive a claim to INVOCATION_RESERVED without ever running a real
    builder -- the exact shape of the owner's reported stuck claim."""
    accept_feature_a(demo)
    claim = reserve(demo)
    with pytest.raises(RuntimeError, match="transport lost"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=lambda _c: (_ for _ in ()).throw(
                RuntimeError("worker transport lost after dispatch")
            ),
        )
    return claim


# --- Case A: reserved, no lease, no builder invocation -> recoverable ----


def test_case_a_reserved_with_no_lease_is_recoverable(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)  # worker store exists and was checked; no lease rows
    terminal = recover(demo, claim)
    assert terminal.state == "ABORTED_NON_EXECUTED"
    assert terminal.token == claim.token
    rows = inspect_claims(store(demo))
    assert len(rows) == 1
    assert rows[0]["state"] == "ABORTED_NON_EXECUTED"
    assert rows[0]["recovery_authorized_by"] == AUTHORIZED_BY
    assert "never reached a worker" in rows[0]["recovery_reason"]
    assert rows[0]["attempt"] == 1


def test_case_a_claimed_but_never_reserved_is_also_recoverable(demo):
    accept_feature_a(demo)
    claim = reserve(demo)
    build_store(demo)
    terminal = recover(demo, claim)
    assert terminal.state == "ABORTED_NON_EXECUTED"


# --- Case B: reserved, worker lease exists -> NOT classifiable as never
# invoked without further evidence -----------------------------------------


def test_case_b_existing_lease_blocks_recovery(demo):
    accept_feature_a(demo)
    claim = reserve(demo)
    build_store(demo)
    put_lease(demo)  # a lease exists even though the claim is only CLAIMED
    with pytest.raises(DispatchFenceError, match="NON_EXECUTION_EVIDENCE_INCONCLUSIVE"):
        recover(demo, claim)
    assert inspect_claims(store(demo))[0]["state"] == "CLAIMED"


# --- Case C: builder started and crashed -> must not be blindly replayed --


def test_case_c_lease_with_no_outcome_is_not_replayable(demo):
    accept_feature_a(demo)
    claim = reserve(demo)
    build_store(demo)
    put_lease(demo)
    with pytest.raises(RuntimeError, match="unknown worker outcome"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=lambda _c: (_ for _ in ()).throw(
                RuntimeError("unknown worker outcome")
            ),
        )
    with pytest.raises(DispatchFenceError, match="NON_EXECUTION_EVIDENCE_INCONCLUSIVE"):
        recover(demo, claim)
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"


# --- Case D: builder completed but result recording failed -> different
# reconciliation path, never this one ---------------------------------------


def test_case_d_result_recorded_claim_is_never_auto_recovered(demo):
    accept_feature_a(demo)
    claim = reserve(demo)
    repo = demo[0]

    def executor(_claim):
        (repo / "feature_b.py").write_text("value = 3\n", encoding="utf-8")
        git(repo, "add", "feature_b.py")
        git(repo, "commit", "-qm", "feature B")
        return git(repo, "rev-parse", "HEAD")

    invoke_once(
        claim=claim,
        claims_path=store(demo),
        owner_state_root=demo[1],
        revalidate=lambda: True,
        executor=executor,
    )
    assert inspect_claims(store(demo))[0]["state"] == "RESULT_RECORDED"
    with pytest.raises(DispatchFenceError, match="NON_EXECUTION_EVIDENCE_INCONCLUSIVE"):
        recover(demo, claim)
    assert inspect_claims(store(demo))[0]["state"] == "RESULT_RECORDED"


def test_case_d_row_state_guard_is_independent_of_the_evidence_check(demo, monkeypatch):
    """Even if evidence were somehow (incorrectly) judged eligible, the
    transition itself refuses to touch a RESULT_RECORDED row -- a second,
    independent guard layer, not just a single check that could drift."""
    import project_orchestrator.local_dispatch_event_claim as module

    accept_feature_a(demo)
    claim = reserve(demo)
    repo = demo[0]

    def executor(_claim):
        (repo / "feature_b.py").write_text("value = 3\n", encoding="utf-8")
        git(repo, "add", "feature_b.py")
        git(repo, "commit", "-qm", "feature B")
        return git(repo, "rev-parse", "HEAD")

    invoke_once(
        claim=claim,
        claims_path=store(demo),
        owner_state_root=demo[1],
        revalidate=lambda: True,
        executor=executor,
    )
    monkeypatch.setattr(
        module,
        "assess_non_execution_evidence",
        lambda **_kwargs: module.NonExecutionEvidence(
            eligible=True, disposition="FORCED", candidate_commit_sha=None, reasons=()
        ),
    )
    with pytest.raises(DispatchFenceError, match="STALE_DISPATCH_CLAIM"):
        recover(demo, claim)
    assert inspect_claims(store(demo))[0]["state"] == "RESULT_RECORDED"


# --- Case E: two recovery processes race -> only one wins -----------------


def test_case_e_concurrent_recovery_only_one_wins(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)

    def attempt(_i):
        try:
            return recover(demo, claim)
        except DispatchFenceError as exc:
            return str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))
    winners = [r for r in outcomes if not isinstance(r, str)]
    losers = [r for r in outcomes if isinstance(r, str)]
    assert len(winners) == 1
    assert len(losers) == 1
    assert "STALE_DISPATCH_CLAIM" in losers[0] or "RECOVERY_RACE_LOST" in losers[0]
    assert len(inspect_claims(store(demo))) == 1
    assert inspect_claims(store(demo))[0]["state"] == "ABORTED_NON_EXECUTED"


# --- Case F: migration or transition interrupted -> no ambiguous/duplicate
# active claims --------------------------------------------------------------


def test_case_f_interrupted_migration_leaves_original_schema_intact(
    demo, monkeypatch, tmp_path
):
    import project_orchestrator.local_dispatch_event_claim as module

    claims_path = tmp_path / "interrupted-claims.sqlite"
    db = _connect(claims_path)
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        """
        CREATE TABLE acceptance_dispatch_claims (
            source_db TEXT NOT NULL,
            owner_root TEXT NOT NULL,
            acceptance_event_id INTEGER NOT NULL,
            milestone_id TEXT NOT NULL,
            repository TEXT NOT NULL,
            slice_id TEXT NOT NULL,
            accepted_sha TEXT NOT NULL,
            claim_token TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('CLAIMED','INVOCATION_RESERVED','RESULT_RECORDED')),
            result_status TEXT,
            claimed_utc TEXT NOT NULL,
            updated_utc TEXT NOT NULL,
            PRIMARY KEY (source_db, acceptance_event_id),
            UNIQUE (source_db, slice_id),
            UNIQUE (claim_token)
        )
        """
    )
    db.execute(
        "INSERT INTO acceptance_dispatch_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "src",
            "root",
            1,
            "M-0",
            "repo",
            "X",
            "a" * 40,
            "t" * 32,
            "CLAIMED",
            None,
            "2026-10-18T00:00:00+00:00",
            "2026-10-18T00:00:00+00:00",
        ),
    )
    db.execute("PRAGMA user_version=1")
    db.execute("COMMIT")
    db.close()

    db = _connect(claims_path)
    monkeypatch.setattr(
        module,
        "_ensure_v2_indexes",
        lambda _db: (_ for _ in ()).throw(
            RuntimeError("simulated crash mid-migration")
        ),
    )
    try:
        with pytest.raises(RuntimeError, match="simulated crash mid-migration"):
            _initialize(db, claims_path)
        # Rolled back: still version 1, original row untouched.
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        row = db.execute(
            "SELECT * FROM acceptance_dispatch_claims WHERE claim_token=?", ("t" * 32,)
        ).fetchone()
        assert row["state"] == "CLAIMED"
    finally:
        db.close()

    monkeypatch.undo()
    db = _connect(claims_path)
    try:
        _initialize(db, claims_path)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        row = db.execute(
            "SELECT * FROM acceptance_dispatch_claims WHERE claim_token=?", ("t" * 32,)
        ).fetchone()
        assert row["state"] == "CLAIMED"
        assert row["attempt"] == 1
    finally:
        db.close()
    # A fresh backup is taken on every attempt that starts from version 1,
    # including the failed one -- always backing up before migrating is
    # the safe default, so the retry after the simulated crash takes a
    # second backup rather than skipping it.
    backups = list(tmp_path.glob("interrupted-claims.sqlite.pre-migration-v1-backup-*"))
    assert len(backups) == 2


def test_case_f_migration_creates_backup_and_passes_integrity_checks(demo, tmp_path):
    claims_path = tmp_path / "v1-claims.sqlite"
    db = _connect(claims_path)
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        """
        CREATE TABLE acceptance_dispatch_claims (
            source_db TEXT NOT NULL,
            owner_root TEXT NOT NULL,
            acceptance_event_id INTEGER NOT NULL,
            milestone_id TEXT NOT NULL,
            repository TEXT NOT NULL,
            slice_id TEXT NOT NULL,
            accepted_sha TEXT NOT NULL,
            claim_token TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('CLAIMED','INVOCATION_RESERVED','RESULT_RECORDED')),
            result_status TEXT,
            claimed_utc TEXT NOT NULL,
            updated_utc TEXT NOT NULL,
            PRIMARY KEY (source_db, acceptance_event_id),
            UNIQUE (source_db, slice_id),
            UNIQUE (claim_token)
        )
        """
    )
    db.execute(
        "INSERT INTO acceptance_dispatch_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "src",
            "root",
            1,
            "M-0",
            "repo",
            "X",
            "a" * 40,
            "t" * 32,
            "INVOCATION_RESERVED",
            None,
            "2026-10-18T00:00:00+00:00",
            "2026-10-18T00:00:00+00:00",
        ),
    )
    db.execute("PRAGMA user_version=1")
    db.execute("COMMIT")
    db.close()

    db = _connect(claims_path)
    try:
        _initialize(db, claims_path)
        row = db.execute(
            "SELECT * FROM acceptance_dispatch_claims WHERE claim_token=?", ("t" * 32,)
        ).fetchone()
        assert row["state"] == "INVOCATION_RESERVED"
        assert row["attempt"] == 1
        assert row["recovered_from_token"] is None
    finally:
        db.close()
    backups = list(tmp_path.glob("v1-claims.sqlite.pre-migration-v1-backup-*"))
    assert len(backups) == 1
    backup_db = sqlite3.connect(backups[0])
    try:
        assert backup_db.execute("PRAGMA user_version").fetchone()[0] == 1
        backup_row = backup_db.execute(
            "SELECT state FROM acceptance_dispatch_claims WHERE claim_token=?",
            ("t" * 32,),
        ).fetchone()
        assert backup_row[0] == "INVOCATION_RESERVED"
    finally:
        backup_db.close()


def test_case_f_active_unique_index_rejects_a_second_simultaneous_active_row(
    demo, tmp_path
):
    """After migration, the real fence is the partial unique index: two
    ACTIVE rows (neither ABORTED_NON_EXECUTED) for the same slice/event
    can never coexist, even via a raw INSERT bypassing claim_accepted_next."""
    claims_path = tmp_path / "fence-claims.sqlite"
    db = _connect(claims_path)
    try:
        _initialize(db, claims_path)
        db.execute(
            "INSERT INTO acceptance_dispatch_claims "
            "(source_db, owner_root, acceptance_event_id, milestone_id, repository, "
            "slice_id, accepted_sha, claim_token, attempt, state, result_status, "
            "recovered_from_token, recovery_authorized_by, recovery_reason, "
            "recovery_evidence_json, claimed_utc, updated_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "src",
                "root",
                1,
                "M-0",
                "repo",
                "X",
                "a" * 40,
                "t" * 32,
                1,
                "CLAIMED",
                None,
                None,
                None,
                None,
                None,
                "2026-10-18T00:00:00+00:00",
                "2026-10-18T00:00:00+00:00",
            ),
        )
        db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO acceptance_dispatch_claims "
                "(source_db, owner_root, acceptance_event_id, milestone_id, repository, "
                "slice_id, accepted_sha, claim_token, attempt, state, result_status, "
                "recovered_from_token, recovery_authorized_by, recovery_reason, "
                "recovery_evidence_json, claimed_utc, updated_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "src",
                    "root",
                    1,
                    "M-0",
                    "repo",
                    "X",
                    "a" * 40,
                    "u" * 32,
                    2,
                    "CLAIMED",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "2026-10-18T00:00:01+00:00",
                    "2026-10-18T00:00:01+00:00",
                ),
            )
            db.commit()
    finally:
        db.close()


# --- Case G: owner control activates during recovery -> stop safely -------


def test_case_g_owner_veto_before_recovery_blocks_it(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    (demo[1] / "human-mode.json").write_text('{"enabled":true}', encoding="utf-8")
    with pytest.raises(DispatchFenceError, match="OWNER_CONTROL_VETO"):
        recover(demo, claim)
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"


def test_case_g_owner_veto_blocks_evidence_free_identity_checks_too(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    (demo[1] / "human-mode.json").write_text('{"enabled":true}', encoding="utf-8")
    with pytest.raises(DispatchFenceError, match="OWNER_CONTROL_VETO"):
        recover(demo, claim, authorized_by="", reason="")


# --- Case H: replacement attempt after proven non-execution ----------------


def test_case_h_replacement_attempt_is_new_token_old_remains_unusable(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    recover(demo, claim)

    new_claim = reserve(demo)
    assert new_claim.token != claim.token
    assert new_claim.event_id == claim.event_id
    assert new_claim.slice_id == claim.slice_id

    rows = {r["claim_token"]: r for r in inspect_claims(store(demo))}
    assert len(rows) == 2
    assert rows[claim.token]["state"] == "ABORTED_NON_EXECUTED"
    assert rows[claim.token]["attempt"] == 1
    assert rows[new_claim.token]["state"] == "CLAIMED"
    assert rows[new_claim.token]["attempt"] == 2
    assert rows[new_claim.token]["recovered_from_token"] == claim.token

    # The old token is permanently unusable for launch.
    with pytest.raises(DispatchFenceError, match="STALE_DISPATCH_CLAIM"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=lambda _c: pytest.fail("must never launch a recovered claim"),
        )

    # A second replacement attempt is correctly refused while the new
    # attempt is still active (the fence still holds for the active row).
    with pytest.raises(DispatchFenceError, match="EVENT_OR_SLICE_ALREADY_CLAIMED"):
        reserve(demo)


def test_case_h_new_attempt_can_be_invoked_normally(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    recover(demo, claim)
    new_claim = reserve(demo)
    repo = demo[0]

    def executor(_claim):
        (repo / "feature_b.py").write_text("value = 3\n", encoding="utf-8")
        git(repo, "add", "feature_b.py")
        git(repo, "commit", "-qm", "feature B via replacement attempt")
        return git(repo, "rev-parse", "HEAD")

    result = invoke_once(
        claim=new_claim,
        claims_path=store(demo),
        owner_state_root=demo[1],
        revalidate=lambda: True,
        executor=executor,
    )
    assert result != demo[5]
    rows = {r["claim_token"]: r for r in inspect_claims(store(demo))}
    assert rows[new_claim.token]["state"] == "RESULT_RECORDED"
    assert rows[claim.token]["state"] == "ABORTED_NON_EXECUTED"


# --- Case I: uncertain execution history -> fails closed -------------------


def test_case_i_missing_worker_store_entirely_is_not_auto_recoverable(demo):
    """The owner's own real KE-core claim's exact shape: the lease database
    file does not exist at all. Its absence could mean 'never created' or
    'evidence lost' -- this module refuses to guess, matching the
    directive's 'a missing lease by itself is not sufficient if other
    evidence leaves execution uncertain.'"""
    claim = reserve_to_invocation_reserved(demo)
    assert not worker_store(demo).exists()
    with pytest.raises(DispatchFenceError, match="NON_EXECUTION_EVIDENCE_INCONCLUSIVE"):
        recover(demo, claim)
    evidence = assess_non_execution_evidence(
        claim=claim,
        state=demo[3],
        refs=demo[2],
        owner_state_root=demo[1],
        claims_path=store(demo),
        worker_db_path=worker_store(demo),
        repo_root=demo[0],
    )
    assert evidence.eligible is False
    assert any("WORKER_STORE_MISSING" in r for r in evidence.reasons)
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"


def test_case_i_existing_candidate_commit_ref_is_not_auto_recoverable(demo):
    """Even with a definitively lease-free reconciliation, a candidate ref
    written for this slice is independent proof that a builder DID produce
    output -- recovery must still refuse."""
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    set_candidate(demo[0], "IG-B", demo[5], expected_current_sha=None)
    with pytest.raises(DispatchFenceError, match="NON_EXECUTION_EVIDENCE_INCONCLUSIVE"):
        recover(demo, claim)
    evidence = assess_non_execution_evidence(
        claim=claim,
        state=demo[3],
        refs=demo[2],
        owner_state_root=demo[1],
        claims_path=store(demo),
        worker_db_path=worker_store(demo),
        repo_root=demo[0],
    )
    assert evidence.eligible is False
    assert any("CANDIDATE_COMMIT_EXISTS" in r for r in evidence.reasons)
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"


def test_case_i_multiple_worker_attempts_is_not_auto_recoverable(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    put_lease(demo, attempt="a1", generation=1)
    put_lease(demo, attempt="a2", generation=2)
    with pytest.raises(DispatchFenceError, match="NON_EXECUTION_EVIDENCE_INCONCLUSIVE"):
        recover(demo, claim)


# --- Input validation: authorization identity and reason are mandatory ----


def test_recovery_requires_a_non_blank_authorization_identity(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    with pytest.raises(
        DispatchFenceError, match="RECOVERY_AUTHORIZATION_IDENTITY_REQUIRED"
    ):
        recover(demo, claim, authorized_by="   ")


def test_recovery_requires_a_non_blank_reason(demo):
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    with pytest.raises(DispatchFenceError, match="RECOVERY_REASON_REQUIRED"):
        recover(demo, claim, reason="")
