import json
from scripts.continuous_flow_supervisor import record_continuation_pass

def test_same_burst_identity_proves_passes_share_one_external_wake(tmp_path):
    burst="wake-001"
    first=record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=False,liveness_reacquire={"requested":False},burst_id=burst)
    second=record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=False,liveness_reacquire={"requested":False},burst_id=burst)
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert first["burst_id"]==second["burst_id"]==burst
    assert [p["burst_id"] for p in history["passes"]]==[burst,burst]

def test_distinct_wakes_cannot_be_misreported_as_one_burst(tmp_path):
    record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=False,liveness_reacquire={"requested":False},burst_id="wake-001")
    record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=False,liveness_reacquire={"requested":False},burst_id="wake-002")
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert history["passes"][0]["burst_id"] != history["passes"][1]["burst_id"]
