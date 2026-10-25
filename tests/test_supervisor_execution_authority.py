import ast
import json
from pathlib import Path
from types import SimpleNamespace

from project_orchestrator import supervisor_runtime

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


def test_default_decision_denies_live_shadow_controller_shape(tmp_path, monkeypatch):
    root = tmp_path / "local" / "ExponentialEngine" / "IronGodLocal"
    root.mkdir(parents=True)
    (root / "controller-handoff-state.json").write_text(json.dumps({
        "controller": "iron-god-handoff-v1",
        "mode": "shadow_state_only",
        "candidate_execution_authorized": False,
    }), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    state = tmp_path / "state"
    state.mkdir()
    assert supervisor_runtime.authorized_dispatch_products(state, ["iron-god"]) == []
    assert "OWNER_AUTHORITY_CONTRACT_UNDEFINED" in record(state)["reasons"]


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
