"""Grant / acknowledge / revoke / replay lifecycle of the owner controller."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from project_orchestrator import iron_god_owner_control as oc

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI owner control")

SHA = "a" * 40
OTHER_SHA = "b" * 40
T0 = datetime(2026, 10, 25, 20, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[1]


def desktop():
    return {"session_id": 1, "origin": "desktop-control"}


def supervisor(name="a"):
    return oc.SupervisorIdentity(instance_id=name * 32, pid=4000 + ord(name), process_start_fingerprint=f"windows:{ord(name)}")


class Alive:
    def __init__(self, *identities):
        self.alive = set(identities)

    def __call__(self, identity):
        return identity in self.alive


@pytest.fixture
def root(tmp_path):
    return tmp_path / "IronGodControl"


def take(root, at=T0, sha=SHA):
    return oc.issue_command(root, oc.TAKE_THE_WHEEL, release_sha=sha, origin_check=desktop, now=at)


def give(root, at=T0):
    return oc.issue_command(root, oc.GIVE_ME_CONTROL, release_sha=None, now=at)


def test_no_enrollment_or_command_is_unverified(root):
    assert oc.evaluate(root).reasons == ("OWNER_NOT_ENROLLED",)
    oc.enroll_owner(root)
    decision = oc.evaluate(root)
    assert (decision.status, decision.permitted, decision.reasons) == (oc.UNVERIFIED, False, ("NO_OWNER_COMMAND",))
    assert oc.owner_status(root) == "OWNER_CONTROL_UNVERIFIED"


def test_take_the_wheel_is_pending_until_a_live_supervisor_acknowledges(root):
    issued = take(root)
    pending = oc.evaluate(root, now=T0 + timedelta(seconds=5))
    assert pending.status == oc.AUTHORIZED_PENDING_ACK
    assert pending.permitted is False
    assert pending.reasons == ("SUPERVISOR_ACK_PENDING",)
    # The supervisor may start during the short pending window ...
    assert oc.owner_status(root, now=T0 + timedelta(seconds=5)) == oc.AUTHORIZED
    sup = supervisor()
    alive = Alive(sup)
    decision = oc.acknowledge_grant(root, sup, release_sha=SHA, now=T0 + timedelta(seconds=30), liveness=alive)
    assert decision.status == oc.AUTHORIZED and decision.permitted is True
    assert decision.command_id == issued["command_id"]
    assert decision.supervisor_instance_id == sup.instance_id
    # ... but only that exact supervisor process holds the grant.
    assert oc.evaluate(root, supervisor=supervisor("b"), now=T0 + timedelta(seconds=40), liveness=Alive(sup, supervisor("b"))).reasons == (
        "GRANT_BOUND_TO_OTHER_SUPERVISOR",
    )
    assert oc.wait_for_ack(root, issued["generation"], timeout_seconds=0) == "ACKNOWLEDGED"


def test_unacknowledged_grant_expires_and_cannot_be_acknowledged_late(root):
    take(root)
    late = T0 + oc.ACK_WINDOW + timedelta(seconds=1)
    sup = supervisor()
    decision = oc.acknowledge_grant(root, sup, release_sha=SHA, now=late, liveness=Alive(sup))
    assert decision.permitted is False
    assert decision.reasons == ("SUPERVISOR_ACK_MISSING",)
    assert oc.owner_status(root, now=late) == "OWNER_CONTROL_UNVERIFIED"


def test_grant_is_bound_to_the_accepted_release(root):
    take(root, sha=SHA)
    sup = supervisor()
    wrong = oc.acknowledge_grant(root, sup, release_sha=OTHER_SHA, now=T0 + timedelta(seconds=10), liveness=Alive(sup))
    assert wrong.reasons == ("GRANT_RELEASE_MISMATCH",)
    right = oc.acknowledge_grant(root, sup, release_sha=SHA, now=T0 + timedelta(seconds=11), liveness=Alive(sup))
    assert right.permitted is True
    assert oc.evaluate(root, release_sha=OTHER_SHA, now=T0 + timedelta(seconds=12), liveness=Alive(sup)).reasons == (
        "GRANT_RELEASE_MISMATCH",
    )


def test_supervisor_restart_never_revives_authority(root):
    take(root)
    first, second = supervisor("a"), supervisor("b")
    assert oc.acknowledge_grant(root, first, release_sha=SHA, now=T0 + timedelta(seconds=10), liveness=Alive(first)).permitted
    # First supervisor exits; a watchdog-started replacement cannot rebind the grant.
    dead = Alive(second)
    assert oc.evaluate(root, now=T0 + timedelta(seconds=60), liveness=dead).reasons == ("ACKNOWLEDGING_SUPERVISOR_GONE",)
    replacement = oc.acknowledge_grant(root, second, release_sha=SHA, now=T0 + timedelta(seconds=61), liveness=dead)
    assert replacement.permitted is False
    assert replacement.reasons == ("GRANT_BOUND_TO_OTHER_SUPERVISOR",)
    assert oc.owner_status(root, now=T0 + timedelta(seconds=61)) == "OWNER_CONTROL_UNVERIFIED"


def test_give_me_control_revokes_immediately_and_is_acknowledged(root):
    take(root)
    sup = supervisor()
    alive = Alive(sup)
    assert oc.acknowledge_grant(root, sup, release_sha=SHA, now=T0 + timedelta(seconds=10), liveness=alive).permitted
    revoke = give(root, at=T0 + timedelta(minutes=5))
    revoked = oc.evaluate(root, supervisor=sup, now=T0 + timedelta(minutes=5), liveness=alive)
    assert (revoked.status, revoked.permitted, revoked.revocation_acknowledged) == (oc.OWNER_CONTROL_ACTIVE, False, False)
    assert oc.owner_status(root, now=T0 + timedelta(minutes=5)) == oc.OWNER_CONTROL_ACTIVE
    # The old grant can never be acknowledged again after revocation.
    again = oc.acknowledge_grant(root, sup, release_sha=SHA, now=T0 + timedelta(minutes=5, seconds=1), liveness=alive)
    assert again.permitted is False and again.status == oc.OWNER_CONTROL_ACTIVE
    assert oc.acknowledge_revocation(root, sup, now=T0 + timedelta(minutes=5, seconds=2), liveness=alive) is True
    acked = oc.evaluate(root, now=T0 + timedelta(minutes=6), liveness=alive)
    assert acked.revocation_acknowledged is True and acked.supervisor_instance_id == sup.instance_id
    assert oc.wait_for_ack(root, revoke["generation"], timeout_seconds=0) == "ACKNOWLEDGED"
    # Only a fresh owner Take the Wheel restores execution.
    take(root, at=T0 + timedelta(minutes=7))
    assert oc.acknowledge_grant(root, sup, release_sha=SHA, now=T0 + timedelta(minutes=7, seconds=5), liveness=alive).permitted


def test_revocation_cannot_be_acknowledged_by_a_dead_supervisor(root):
    take(root)
    give(root, at=T0 + timedelta(seconds=30))
    assert oc.acknowledge_revocation(root, supervisor(), now=T0 + timedelta(seconds=31), liveness=Alive()) is False
    assert oc.evaluate(root, now=T0 + timedelta(seconds=32)).revocation_acknowledged is False


def _rows(root):
    with closing(sqlite3.connect(root / oc.DB_FILE)) as db:
        return db.execute("SELECT generation, payload, mac FROM owner_commands ORDER BY generation").fetchall()


def test_replayed_reordered_forged_and_stale_commands_fail(root):
    take(root)
    generation, payload, mac = _rows(root)[0]
    now = T0 + timedelta(seconds=10)
    with pytest.raises(oc.OwnerControlError, match="COMMAND_GENERATION_STALE"):
        oc.submit_signed_command(root, payload, mac, now=now)
    forged = json.loads(payload) | {"generation": generation + 1, "command_id": "f" * 32}
    forged_text = oc._canonical(forged)
    with pytest.raises(oc.OwnerControlError, match="AUTHENTICATION_FAILED"):
        oc.submit_signed_command(root, forged_text, mac, now=now)
    key = oc._owner_key(root)
    with pytest.raises(oc.OwnerControlError, match="COMMAND_NOT_FRESH"):
        oc.submit_signed_command(root, forged_text, oc._mac(key, forged_text), now=T0 + timedelta(minutes=10))
    duplicate_id = json.loads(payload) | {"generation": generation + 1}
    duplicate_text = oc._canonical(duplicate_id)
    with pytest.raises(oc.OwnerControlError, match="COMMAND_REPLAYED"):
        oc.submit_signed_command(root, duplicate_text, oc._mac(key, duplicate_text), now=now)


def test_tampered_or_unauthenticated_rows_grant_nothing(root):
    give(root)
    with closing(sqlite3.connect(root / oc.DB_FILE)) as db:
        payload = json.loads(db.execute("SELECT payload FROM owner_commands").fetchone()[0])
        payload["action"] = oc.TAKE_THE_WHEEL
        payload["release_sha"] = SHA
        db.execute("UPDATE owner_commands SET action='TAKE_THE_WHEEL', payload=?", (oc._canonical(payload),))
        db.commit()
    assert oc.evaluate(root, now=T0).reasons == ("AUTHENTICATION_FAILED",)
    with closing(sqlite3.connect(root / oc.DB_FILE)) as db:
        db.execute("DELETE FROM owner_commands")
        db.execute(
            "INSERT INTO owner_commands VALUES (1, ?, 'TAKE_THE_WHEEL', ?, ?, ?)",
            ("c" * 32, oc._canonical(payload), "0" * 64, T0.isoformat()),
        )
        db.commit()
    assert oc.evaluate(root, now=T0).permitted is False
    assert oc.owner_status(root, now=T0) == "OWNER_CONTROL_UNVERIFIED"


def test_tampered_acknowledgment_is_rejected(root):
    take(root)
    sup = supervisor()
    oc.acknowledge_grant(root, sup, release_sha=SHA, now=T0 + timedelta(seconds=5), liveness=Alive(sup))
    with closing(sqlite3.connect(root / oc.DB_FILE)) as db:
        text = db.execute("SELECT payload FROM supervisor_acks").fetchone()[0]
        ack = json.loads(text)
        ack["supervisor"]["instance_id"] = "z" * 32
        db.execute("UPDATE supervisor_acks SET payload=?", (oc._canonical(ack),))
        db.commit()
    assert oc.evaluate(root, now=T0 + timedelta(seconds=6), liveness=Alive(sup)).reasons == ("ACK_AUTHENTICATION_FAILED",)


def test_retired_markers_and_arbitrary_json_are_not_authority(root, tmp_path):
    flow = tmp_path / "flow-state"
    flow.mkdir()
    (flow / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    (flow / "iron-god-exclusive.json").write_text('{"enabled": true, "target_product": "iron-god"}', encoding="utf-8")
    root.mkdir()
    (root / "authorization.json").write_text('{"permitted": true}', encoding="utf-8")
    assert oc.control_root(flow) == root
    assert oc.evaluate(oc.control_root(flow)).permitted is False
    source = Path(oc.__file__).read_text(encoding="utf-8")
    assert "human-mode.json" not in source and "iron-god-exclusive" not in source


def test_grant_expires_after_maximum_age(root):
    take(root)
    sup = supervisor()
    alive = Alive(sup)
    oc.acknowledge_grant(root, sup, release_sha=SHA, now=T0 + timedelta(seconds=5), liveness=alive)
    assert oc.evaluate(root, now=T0 + oc.GRANT_MAX_AGE + timedelta(seconds=1), liveness=alive).reasons == ("GRANT_EXPIRED",)


def test_unsafe_control_directory_acl_fails_closed(root):
    take(root)
    subprocess.run(["icacls", str(root), "/grant", "*S-1-5-32-545:(OI)(CI)R", "/Q"], check=True, capture_output=True)
    assert oc.evaluate(root, now=T0).reasons == ("CONTROL_DIRECTORY_ACL_UNSAFE",)


def test_replaced_key_invalidates_previous_commands(root):
    take(root)
    (root / oc.KEY_FILE).write_bytes(oc._dpapi(b"k" * 32, protect=True))
    assert oc.evaluate(root, now=T0).reasons == ("OWNER_KEY_MISMATCH",)


def test_wait_for_ack_reports_supersession_and_timeout(root):
    first = take(root)
    assert oc.wait_for_ack(root, first["generation"], timeout_seconds=0) == "TIMEOUT"
    give(root, at=T0 + timedelta(seconds=1))
    assert oc.wait_for_ack(root, first["generation"], timeout_seconds=0) == "SUPERSEDED"


def test_real_supervisor_liveness_uses_pid_and_start_time():
    me = oc.current_supervisor_identity("i" * 32)
    assert oc.supervisor_alive(me) is True
    assert oc.supervisor_alive(oc.SupervisorIdentity(me.instance_id, me.pid, "windows:1", me.host)) is False
    assert oc.supervisor_alive(oc.SupervisorIdentity(me.instance_id, me.pid, me.process_start_fingerprint, "elsewhere")) is False


def test_non_desktop_process_can_revoke_but_never_grant(root):
    with pytest.raises(oc.OwnerControlError) as refused:
        oc.issue_command(root, oc.TAKE_THE_WHEEL, release_sha=SHA, now=T0)
    assert refused.value.code in {"NOT_DESKTOP_CONTROL_ORIGIN", "NOT_ACTIVE_CONSOLE_SESSION"}
    assert not (root / oc.DB_FILE).exists() or oc.evaluate(root).reasons == ("NO_OWNER_COMMAND",)
    revoked = give(root)
    assert revoked["action"] == oc.GIVE_ME_CONTROL
    assert oc.evaluate(root, now=T0).status == oc.OWNER_CONTROL_ACTIVE


def test_cli_issue_from_a_non_desktop_process_is_refused(root, tmp_path):
    pin = tmp_path / "pin.json"
    pin.write_text(json.dumps({"accepted_sha": SHA}), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "project_orchestrator.iron_god_owner_control", "--root", str(root),
         "issue", "--action", "TAKE_THE_WHEEL", "--pin", str(pin)],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "REFUSED"
    status = subprocess.run(
        [sys.executable, "-m", "project_orchestrator.iron_god_owner_control", "--root", str(root), "status"],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )
    assert status.returncode == 1
    assert json.loads(status.stdout)["permitted"] is False


def test_decision_survives_a_process_restart_without_new_authority(root):
    take(root, at=datetime.now(UTC))
    me = oc.current_supervisor_identity("r" * 32)
    assert oc.acknowledge_grant(root, me, release_sha=SHA).permitted is True
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    code = (
        "import json,sys; from pathlib import Path;"
        "from project_orchestrator import iron_god_owner_control as oc;"
        f"d=oc.evaluate(Path({str(root)!r}));print(json.dumps(d.as_dict()))"
    )
    other = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60, check=True)
    seen = json.loads(other.stdout)
    # Another process observes the same live grant, bound to this supervisor.
    assert seen["permitted"] is True and seen["supervisor_instance_id"] == me.instance_id
