import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "laptop_actor_github_bridge.py"
SPEC = importlib.util.spec_from_file_location("laptop_actor_github_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)

SHA = "a" * 40


def job(**overrides):
    value = {
        "job_id": "job-1",
        "packet_id": "packet-1",
        "repository": "jweter/knowledge-engine-core",
        "branch": "orchestrator/laptop-proof",
        "base_sha": SHA,
        "verification": ["python -m pytest -q"],
        "merge_authority": False,
        "product_reality_authority": False,
    }
    value.update(overrides)
    return value


def test_remote_job_accepts_product_portfolio_and_control_plane_feature_branch():
    assert bridge.validate_remote_job(job())["repository"] == "jweter/knowledge-engine-core"
    control = job(repository="jweter/project-orchestrator", branch="orchestrator/vnext-learning-engine")
    assert bridge.validate_remote_job(control)["repository"] == "jweter/project-orchestrator"
    with pytest.raises(ValueError):
        bridge.validate_remote_job(job(repository="jweter/project-orchestrator", branch="main"))
    with pytest.raises(ValueError):
        bridge.validate_remote_job(job(repository="jweter/not-authorized"))
    with pytest.raises(ValueError):
        bridge.validate_remote_job(job(base_sha="main"))


def test_legacy_packet_id_is_deterministically_normalized_to_job_id():
    legacy = job()
    legacy.pop("job_id")
    normalized = bridge.validate_remote_job(legacy)
    assert normalized["job_id"] == normalized["packet_id"] == "packet-1"


def test_remote_job_cannot_grant_merge_or_product_reality_authority():
    with pytest.raises(ValueError):
        bridge.validate_remote_job(job(merge_authority=True))
    with pytest.raises(ValueError):
        bridge.validate_remote_job(job(product_reality_authority=True))


def test_ke_native_adapter_is_bound_to_core_and_allowlisted_checks():
    native = job(verification=[], execution_adapter="knowledge_engine_core", requested_checks=["preflight", "ollama_health"])
    assert bridge.validate_remote_job(native)["requested_checks"] == ["preflight", "ollama_health"]
    with pytest.raises(ValueError):
        bridge.validate_remote_job({**native, "repository": "jweter/knowledge-engine-web"})
    with pytest.raises(ValueError):
        bridge.validate_remote_job({**native, "requested_checks": ["preflight", "arbitrary_shell"]})


def test_remote_job_must_have_generic_or_native_execution_plan():
    with pytest.raises(ValueError):
        bridge.validate_remote_job(job(verification=[]))


def test_sanitized_result_never_exports_raw_runner_error_or_authority():
    result = bridge.sanitize_result({"job_id": "job-1", "packet_id": "packet-1", "repository": "jweter/knowledge-engine-core", "branch": "orchestrator/laptop-proof", "base_sha": SHA, "exact_head_sha": SHA, "status": "ENVIRONMENT_FAILURE", "failure_class": "WORKSPACE_CLONE_FAILED", "changed_paths": [], "verification": [{"runner_error": r"C:\\Users\\private\\secret path"}], "evidence_ref": "local://private/path", "merged": True, "product_reality_verified": True, "raw_log": "secret"})
    assert result["verification"] == [{"runner_error": "local runner environment failure"}]
    assert result["failure_class"] == "WORKSPACE_CLONE_FAILED"
    assert "raw_log" not in result and "merged" not in result
    assert result["private_payload_exported"] is False
    assert result["merge_authority"] is False
    assert result["product_reality_authority"] is False


def test_sanitized_runner_error_code_is_diagnostic_but_path_free():
    result = bridge.sanitize_result({"job_id": "job-1", "packet_id": "packet-1", "repository": "jweter/knowledge-engine-core", "branch": "orchestrator/laptop-proof", "base_sha": SHA, "exact_head_sha": SHA, "status": "ENVIRONMENT_FAILURE", "changed_paths": [], "verification": [{"runner_error_code": "EXACT_BASE_UNAVAILABLE"}], "evidence_ref": "local://laptop-actor/job-1"})
    assert result["verification"] == [{"runner_error_code": "EXACT_BASE_UNAVAILABLE"}]


def test_sanitized_command_result_keeps_only_bounded_command_and_returncode():
    result = bridge.sanitize_result({"job_id": "job-1", "packet_id": "packet-1", "repository": "jweter/Project-Everward", "branch": "orchestrator/laptop-proof", "base_sha": SHA, "exact_head_sha": SHA, "status": "PASS", "changed_paths": [], "verification": [{"command": "python tools/quality_preflight.py --full", "returncode": 0, "stdout": "private"}], "evidence_ref": "local://laptop-actor/job-1"})
    assert result["verification"] == [{"command": "python tools/quality_preflight.py --full", "returncode": 0}]


def test_queue_is_dedicated_non_default_branch():
    assert bridge.QUEUE_BRANCH == "laptop-actor-queue"
    assert bridge.PENDING_DIR.startswith("engineering/laptop-queue/")
    assert bridge.RESULTS_DIR.startswith("engineering/laptop-queue/")
