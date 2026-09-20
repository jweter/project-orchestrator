import json
from datetime import UTC, datetime
import pytest
import scripts.continuous_flow_supervisor as supervisor

@pytest.mark.parametrize("patch",[
    {"schema_version":999},
    {"burst_id":""},
    {"burst_id":"x"*129},
    {"pass_count":-1},
    {"pass_count":1000001},
    {"pass_count":True},
    {"process_instance_id":""},
    {"process_instance_id":"x"*129},
])
def test_malformed_v2_burst_state_starts_new_burst(tmp_path,patch):
    now=datetime.now(UTC)
    prior={"schema_version":2,"burst_id":"old","process_instance_id":"old-instance",
           "pass_count":7,"immediate_continuation":True,"updated_at_utc":now.isoformat()}
    prior.update(patch)
    (tmp_path/"continuation-current-burst.json").write_text(json.dumps(prior))
    result=supervisor.start_or_resume_burst(tmp_path,process_instance_id="new-instance",now=now)
    assert result["resumed"] is False
    assert result["burst_pass"]==0
    assert result["process_instance_id"]=="new-instance"

def test_valid_v2_state_resumes(tmp_path):
    now=datetime.now(UTC)
    prior={"schema_version":2,"burst_id":"old","process_instance_id":"old-instance",
           "pass_count":7,"immediate_continuation":True,"updated_at_utc":now.isoformat()}
    (tmp_path/"continuation-current-burst.json").write_text(json.dumps(prior))
    result=supervisor.start_or_resume_burst(tmp_path,process_instance_id="new-instance",now=now)
    assert result["resumed"] is True
    assert result["burst_id"]=="old"
    assert result["burst_pass"]==7

def test_legacy_v1_state_can_resume_without_process_identity(tmp_path):
    now=datetime.now(UTC)
    prior={"schema_version":1,"burst_id":"old","pass_count":2,
           "immediate_continuation":True,"updated_at_utc":now.isoformat()}
    (tmp_path/"continuation-current-burst.json").write_text(json.dumps(prior))
    result=supervisor.start_or_resume_burst(tmp_path,process_instance_id="new-instance",now=now)
    assert result["resumed"] is True
    assert result["previous_process_instance_id"] is None
