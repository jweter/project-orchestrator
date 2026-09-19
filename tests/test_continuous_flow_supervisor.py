from scripts.continuous_flow_supervisor import atomic
def test_atomic_supervisor_state(tmp_path):
    path=tmp_path/"state.json"; atomic(path,{"status":"HEALTHY"})
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '"status": "HEALTHY"' in path.read_text(encoding="utf-8")
