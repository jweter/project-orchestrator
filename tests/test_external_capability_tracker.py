import pytest
from project_orchestrator.external_capability_tracker import (
    CapabilityWorkItem, WorkState, next_todo, summarize, validate_board,
)

def item(name="OpenSpec", slice="architecture", state=WorkState.TODO, **kw):
    return CapabilityWorkItem(name, slice, state, **kw)

def test_done_requires_evidence():
    with pytest.raises(ValueError, match="lacks evidence"):
        validate_board([item(state=WorkState.DONE)])

def test_working_requires_next_gate():
    with pytest.raises(ValueError, match="lacks next gate"):
        validate_board([item(state=WorkState.WORKING)])

def test_duplicate_work_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        validate_board([item(), item()])

def test_summary_preserves_done_working_todo_buckets():
    board=[item(state=WorkState.DONE,evidence="PR #163"), item("Headroom","benchmark",WorkState.WORKING,next_gate="baseline"), item("Docling","ingestion",WorkState.TODO)]
    result=summarize(board)
    assert [x["name"] for x in result["DONE"]]==["OpenSpec"]
    assert [x["name"] for x in result["WORKING"]]==["Headroom"]
    assert [x["name"] for x in result["TODO"]]==["Docling"]

def test_current_work_precedes_new_todo():
    current=item("Headroom","benchmark",WorkState.WORKING,next_gate="baseline")
    assert next_todo([item(),current]) == current
