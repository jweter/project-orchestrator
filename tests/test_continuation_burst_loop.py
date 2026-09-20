from scripts.continuous_flow_supervisor import continuation_delay

def test_successful_nonterminal_builder_reenters_immediately():
    assert continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":False},
        builder_ok=True,builder_status="PR_OPEN",normal_seconds=300)==1

def test_no_work_uses_normal_bounded_delay():
    assert continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":False},
        builder_ok=True,builder_status="NO_WORK",normal_seconds=300)==300

def test_failed_builder_does_not_busy_loop():
    assert continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":False},
        builder_ok=False,builder_status="PROCESS_FAILURE",normal_seconds=10)==60

def test_lifecycle_advance_reenters_even_when_builder_terminal():
    assert continuation_delay(lifecycle_advanced=True,liveness_reacquire={"requested":False},
        builder_ok=True,builder_status="NO_WORK",normal_seconds=300)==1

def test_liveness_reacquire_reenters_even_when_builder_failed():
    assert continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":True},
        builder_ok=False,builder_status="PROCESS_FAILURE",normal_seconds=300)==1
