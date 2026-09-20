import json
from scripts.continuous_flow_supervisor import continuation_delay, record_continuation_pass

def test_single_wake_can_prove_two_immediate_productive_passes(tmp_path):
    for status in ("PR_OPEN","PR_OPEN"):
        delay=continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":False},
            builder_ok=True,builder_status=status,normal_seconds=300)
        assert delay==1
        record_continuation_pass(tmp_path,builder_status=status,delay_seconds=delay,
            lifecycle_advanced=False,liveness_reacquire={"requested":False})
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert history["pass_count"]==2
    assert history["immediate_continuation_count"]==2
    assert [p["sequence"] for p in history["passes"]]==[1,2]

def test_nonproductive_second_pass_breaks_immediate_burst(tmp_path):
    first=continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":False},
        builder_ok=True,builder_status="PR_OPEN",normal_seconds=300)
    record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=first,
        lifecycle_advanced=False,liveness_reacquire={"requested":False})
    second=continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":True},
        builder_ok=True,builder_status="NO_OP",normal_seconds=300)
    record_continuation_pass(tmp_path,builder_status="NO_OP",delay_seconds=second,
        lifecycle_advanced=False,liveness_reacquire={"requested":True})
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert first==1 and second==300
    assert history["immediate_continuation_count"]==1
    assert history["passes"][-1]["immediate_continuation"] is False

def test_history_is_bounded_but_total_count_is_durable(tmp_path):
    for i in range(105):
        record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
            lifecycle_advanced=False,liveness_reacquire={"requested":False})
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert history["pass_count"]==105
    assert len(history["passes"])==100
    assert history["passes"][-1]["sequence"]==105
