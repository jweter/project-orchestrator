import io
import json
from pathlib import Path

import pytest
from project_orchestrator.claude_dispatch import dispatch, record_outcome, summarize_measurements

class Response:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps({"claude_code_session_id":"cse_test","claude_code_session_url":"https://claude.ai/code/cse_test"}).encode()

def test_dispatch_uses_secret_without_persisting_it_and_blocks_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_ROUTINE_PORTFOLIO_BUDER_TOKEN", "unused")
    monkeypatch.setenv("CLAUDE_ROUTINE_PORTFOLIO_BUILDER_TOKEN", "super-secret")
    seen = {}
    def open_(req, timeout):
        seen["auth"] = req.headers["Authorization"]
        return Response()
    rec = dispatch("portfolio_builder","jweter/knowledge-engine-core","issue-402","do work",tmp_path,opener=open_)
    assert rec.session_id == "cse_test"
    assert "super-secret" not in next(tmp_path.glob("*.json")).read_text()
    assert seen["auth"] == "Bearer super-secret"
    with pytest.raises(RuntimeError, match="duplicate Claude dispatch blocked"):
        dispatch("portfolio_builder","jweter/knowledge-engine-core","issue-402","do work",tmp_path,opener=open_)

def test_measurement_layer_records_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_ROUTINE_REPAIR_RESCUE_TOKEN","secret")
    rec=dispatch("repair_rescue","jweter/Project-Everward","ci-123","repair",tmp_path,opener=lambda *a,**k: Response())
    record_outcome(tmp_path,rec.dispatch_id,status="PASS",product_advancement=True,human_intervention=False,repair_iterations=1)
    summary=summarize_measurements(tmp_path)
    assert summary == {"dispatches":1,"completed":1,"advanced":1,"human_interventions":0,"advancement_rate":1.0}

def test_unknown_routine_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="unknown Claude routine"):
        dispatch("anything","repo","task","x",tmp_path)
