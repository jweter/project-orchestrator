import json
from pathlib import Path
import scripts.autonomous_slice_builder as builder

def write_grant(tmp_path,runner="aider-ollama",runtime="laptop-actor"):
    p=tmp_path/"g.json"
    p.write_text(json.dumps({"schema_version":5,"grant_id":"g","execution_slice":{
      "runner_id":runner,"execution_runtime":runtime}}))
    return p

def test_v5_grant_execution_identity_is_read_exactly(tmp_path):
    p=write_grant(tmp_path)
    assert builder.grant_execution_identity(p)==("aider-ollama","laptop-actor")
    assert builder.execution_identity_matches(p,"aider-ollama","laptop-actor")
    assert not builder.execution_identity_matches(p,"aider-ollama","native-windows")

def test_pre_v5_or_missing_identity_fails_closed(tmp_path):
    p=tmp_path/"old.json"; p.write_text(json.dumps({"schema_version":4,"execution_slice":{}}))
    assert builder.grant_execution_identity(p) is None
    assert not builder.execution_identity_matches(p,"aider-ollama","native-windows")
