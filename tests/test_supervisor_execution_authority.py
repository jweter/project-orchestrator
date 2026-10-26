import ast
import json
from pathlib import Path
from types import SimpleNamespace

from project_orchestrator import iron_god_owner_control as oc
from project_orchestrator import supervisor_runtime
from tests.owner_authority import TEST_RELEASE_SHA, grant_owner_authority, revoke_owner_authority

SUPERVISOR = Path(__file__).resolve().parents[1] / "scripts" / "continuous_flow_supervisor.py"


def record(state):
    return json.loads((state / "supervisor-execution-authority.json").read_text(encoding="utf-8"))


def test_denied_decision_dispatches_nothing_and_records_reasons(tmp_path):
    denied = SimpleNamespace(
        permitted=False, reasons=("CONTROLLER_SHADOW_MODE", "OWNER_AUTHORITY_CONTRACT_UNDEFINED")
    )
    assert supervisor_runtime.authorized_dispatch_products(
        tmp_path, ["iron-god", "rocksmith"], lambda: denied
    ) == []
    payload = record(tmp_path)
    assert payload["permitted"] is False
    assert payload["reasons"] == ["CONTROLLER_SHADOW_MODE", "OWNER_AUTHORITY_CONTRACT_UNDEFINED"]
    assert payload["candidate_products"] == ["iron-god", "rocksmith"]


def test_permitted_decision_preserves_readiness_selection(tmp_path):
    granted = SimpleNamespace(permitted=True, reasons=())
    assert supervisor_runtime.authorized_dispatch_products(
        tmp_path, ["iron-god"], lambda: granted
    ) == ["iron-god"]
    assert record(tmp_path)["permitted"] is True


def test_failing_or_malformed_decision_denies(tmp_path):
    def broken():
        raise OSError("controller unreadable")

    assert supervisor_runtime.authorized_dispatch_products(tmp_path, ["iron-god"], broken) == []
    assert record(tmp_path)["reasons"] == ["EXECUTION_AUTHORITY_UNVERIFIED"]
    for decision in (None, SimpleNamespace(permitted="yes"), SimpleNamespace(permitted=1)):
        assert supervisor_runtime.authorized_dispatch_products(
            tmp_path, ["iron-god"], lambda d=decision: d
        ) == []
        assert record(tmp_path)["reasons"] == ["EXECUTION_AUTHORITY_DENIED"]


def test_default_decision_denies_without_an_owner_grant(tmp_path):
    """Today's live shape: no owner Take the Wheel recorded for this state root."""
    state = tmp_path / "flow-state"
    state.mkdir()
    me = oc.current_supervisor_identity("d" * 32).as_dict()
    assert supervisor_runtime.authorized_dispatch_products(
        state, ["iron-god"], supervisor=me, release_sha=TEST_RELEASE_SHA
    ) == []
    assert record(state)["reasons"] == ["OWNER_NOT_ENROLLED"]


def test_default_decision_requires_supervisor_identity_and_release(tmp_path):
    state = tmp_path / "flow-state"
    grant_owner_authority(state)
    assert supervisor_runtime.authorized_dispatch_products(state, ["iron-god"]) == []
    assert record(state)["reasons"] == ["EXECUTION_AUTHORITY_UNVERIFIED"]


def test_default_decision_acknowledges_and_permits_the_owner_grant(tmp_path):
    state = tmp_path / "flow-state"
    root = oc.control_root(state)
    oc.issue_command(root, oc.TAKE_THE_WHEEL, release_sha=TEST_RELEASE_SHA,
                     origin_check=lambda: {"session_id": 1, "origin": "desktop-control"})
    me = oc.current_supervisor_identity("e" * 32).as_dict()
    assert supervisor_runtime.authorized_dispatch_products(
        state, ["iron-god"], supervisor=me, release_sha=TEST_RELEASE_SHA
    ) == ["iron-god"]
    payload = record(state)
    assert payload["permitted"] is True and payload["supervisor_instance_id"] == me["instance_id"]
    # A different release is never dispatched under this grant.
    assert supervisor_runtime.authorized_dispatch_products(
        state, ["iron-god"], supervisor=me, release_sha="b" * 40
    ) == []


def test_owner_reclaimed_acknowledges_revocation_and_stops(tmp_path):
    state = tmp_path / "flow-state"
    root = oc.control_root(state)
    oc.issue_command(root, oc.TAKE_THE_WHEEL, release_sha=TEST_RELEASE_SHA,
                     origin_check=lambda: {"session_id": 1, "origin": "desktop-control"})
    me = oc.current_supervisor_identity("f" * 32).as_dict()
    assert supervisor_runtime.owner_reclaimed(state, me, TEST_RELEASE_SHA, process_instance_id="p") is False
    revoke_owner_authority(state)
    assert supervisor_runtime.owner_reclaimed(state, me, TEST_RELEASE_SHA, process_instance_id="p") is True
    human = json.loads((state / "supervisor-human-ownership.json").read_text(encoding="utf-8"))
    assert human["owner_status"] == "OWNER_CONTROL_ACTIVE" and human["revocation_acknowledged"] is True
    assert oc.evaluate(root).revocation_acknowledged is True


def test_supervisor_main_routes_every_dispatch_through_the_gate():
    """Readiness alone must never reach dispatch_parallel_builders."""
    tree = ast.parse(SUPERVISOR.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    gated = []
    dispatches = []
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "authorized_dispatch_products":
                gated.append(node.lineno)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "dispatch_parallel_builders":
            dispatches.append(node.lineno)
    assert len(gated) == 1
    gate_line = gated[0]
    assert dispatches and all(line > gate_line for line in dispatches)
    # products is only reassigned (narrowed) after the gate, never widened.
    for node in ast.walk(main):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "products" for t in node.targets
        ):
            assert node.lineno >= gate_line or "evolution_authorized" in ast.unparse(node.value)


def test_supervisor_commissions_exactly_one_worker():
    """Owner directive: one supervisor + one worker until a full authorized
    single-worker cycle is verified; parallelism is not yet permitted."""
    assert supervisor_runtime.COMMISSIONING_WORKER_LIMITS == {
        "max_workers_per_product": 1,
        "max_total_workers": 1,
    }
    tree = ast.parse(SUPERVISOR.read_text(encoding="utf-8"))
    pools = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ParallelWorkerPool"
    ]
    assert len(pools) == 1
    assert not pools[0].args
    assert [ast.unparse(k.value) for k in pools[0].keywords] == [
        "_supervisor_runtime.COMMISSIONING_WORKER_LIMITS"
    ]
    assert pools[0].keywords[0].arg is None  # passed as **limits, nothing else


def test_commissioning_pool_admits_a_single_worker():
    from project_orchestrator.parallel_worker_pool import ParallelWorkerPool

    pool = ParallelWorkerPool(**supervisor_runtime.COMMISSIONING_WORKER_LIMITS)
    try:
        assert pool.max_workers_per_product == 1
        assert pool.max_total_workers == 1
    finally:
        pool.shutdown(wait=False)
