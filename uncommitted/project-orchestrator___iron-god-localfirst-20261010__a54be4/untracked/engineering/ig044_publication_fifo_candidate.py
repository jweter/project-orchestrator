"""IG-044: atomic FIFO publication claim; local-only candidate, not release authority."""
from __future__ import annotations
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

class PublicationClaimError(ValueError):
    pass

def claim_oldest_publication(db_path: Path, repository: str) -> dict | None:
    """Claim the oldest unpublished milestone; never authorize GitHub transport."""
    if not isinstance(repository, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository) is None:
        raise PublicationClaimError("INVALID_REPOSITORY")
    db = sqlite3.connect(Path(db_path), timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        bad = db.execute(
            "SELECT 1 FROM publications p LEFT JOIN milestones m ON m.milestone_id=p.milestone_id "
            "WHERE p.repository=? AND p.state<>'RELEASE_SYNCED' AND "
            "(m.milestone_id IS NULL OR m.repository<>p.repository OR "
            "(SELECT COUNT(*) FROM journal j WHERE j.milestone_id=p.milestone_id AND j.event_type='MILESTONE_ACCEPTED')<>1 OR "
            "(SELECT COUNT(*) FROM journal j WHERE j.milestone_id=p.milestone_id AND j.event_type='MILESTONE_ACCEPTED' AND j.detail=m.accepted_sha)<>1) LIMIT 1",
            (repository,),
        ).fetchone()
        if bad is not None:
            db.rollback()
            return None
        pending = db.execute(
            "SELECT 1 FROM acceptance_intents WHERE repository=? AND state='PREPARED' LIMIT 1",
            (repository,),
        ).fetchone()
        if pending is not None:
            db.rollback()
            return None
        active = db.execute(
            "SELECT 1 FROM publications WHERE repository=? AND state IN "
            "('PUSHING','PR_OPEN','CI_PENDING','REVIEW_PENDING','MERGE_READY') LIMIT 1",
            (repository,),
        ).fetchone()
        if active is not None:
            db.rollback()
            return None
        oldest = db.execute(
            "SELECT p.milestone_id,p.repository,p.state,m.accepted_sha,m.verification_sha256,"
            "m.product_reality,m.accepted_at FROM publications p "
            "JOIN milestones m ON m.milestone_id=p.milestone_id "
            "JOIN journal j ON j.milestone_id=p.milestone_id AND j.event_type='MILESTONE_ACCEPTED' "
            "WHERE p.repository=? AND p.state<>'RELEASE_SYNCED' ORDER BY j.event_id LIMIT 1",
            (repository,),
        ).fetchone()
        if oldest is None or oldest["state"] not in ("READY", "DEFERRED"):
            db.rollback()
            return None
        now = datetime.now(timezone.utc).isoformat()
        updated = db.execute(
            "UPDATE publications SET state='PUSHING',last_error=NULL,changed_at=? "
            "WHERE milestone_id=? AND state=?",
            (now, oldest["milestone_id"], oldest["state"]),
        )
        if updated.rowcount != 1:
            raise PublicationClaimError("PUBLICATION_STATE_CHANGED")
        db.execute(
            "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) VALUES (?,?,?,?)",
            (oldest["milestone_id"], "PUBLICATION_PUSHING", "FIFO_CLAIM", now),
        )
        db.commit()
        result = dict(oldest)
        result["previous_state"] = result.pop("state")
        result["state"] = "PUSHING"
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
