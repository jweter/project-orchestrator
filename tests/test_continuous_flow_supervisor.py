from scripts.continuous_flow_supervisor import atomic
def test_atomic_supervisor_state(tmp_path):
    path=tmp_path/"state.json"; atomic(path,{"status":"HEALTHY"})
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '"status": "HEALTHY"' in path.read_text(encoding="utf-8")


def test_linear_reconciliation_is_optional(tmp_path):
    from scripts.continuous_flow_supervisor import run_linear_reconciliation
    assert run_linear_reconciliation(tmp_path,tmp_path/"state") is None
