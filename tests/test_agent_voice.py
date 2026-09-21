from scripts.agent_voice import WORKER_PROMPT_VOICE, manager_status, worker_status


def test_worker_voice_is_explicitly_presentation_only():
    lower = WORKER_PROMPT_VOICE.lower()
    assert "operator-facing narration only" in lower
    assert "never use this dialect in source code" in lower
    assert "machine-readable output" in lower


def test_worker_blocked_voice_is_miner_register():
    line = worker_status("NO_READY_EXECUTION_SLICE", "CAPABILITY_GRAPH_REQUIRED")
    assert "pick" in line
    assert "nowt" in line
    assert "CAPABILITY_GRAPH_REQUIRED" in line


def test_manager_idle_voice_is_radio_business_register():
    line = manager_status(False, "ELIGIBLE_IDLE", "PROCESS_FAILURE")
    assert "listeners" in line
    assert "Azathoth Engineering Company" in line
    assert "order desk" in line
    assert "PROCESS_FAILURE" in line


def test_manager_healthy_voice_reports_business():
    line = manager_status(True, "PRODUCTIVE", "PR_OPEN")
    assert "on the air" in line
    assert "production is moving" in line
