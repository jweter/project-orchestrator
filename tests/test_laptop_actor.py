import pytest

from project_orchestrator.laptop_actor import build_laptop_job, next_actor_action, validate_laptop_result

SHA_A = "a" * 40
SHA_B = "b" * 40


def packet(**overrides):
    value = {
        "packet_id": "packet-243",
        "project": "everward",
        "repository": "jweter/Project-Everward",
        "source_ref": "issue-243",
        "base_sha": SHA_A,
        "branch": "orchestrator/unattended-windows-243",
        "paths": ["scripts/verify_windows.ps1"],
        "task": "Run bounded unattended Windows verification",
        "verification": ["powershell -File scripts/verify_windows.ps1"],
    }
    value.update(overrides)
    return value


def test_job_is_deterministic_and_unattended():
    first = build_laptop_job(packet(), workspace_root="C:/Projects")
    second = build_laptop_job(packet(), workspace_root="C:/Projects")
    assert first["job_id"] == second["job_id"]
    assert first["execution_mode"] == "UNATTENDED_IDLE"
    assert first["prohibitions"]["merge"] is True


def test_actor_rejects_control_plane_as_product_work():
    with pytest.raises(ValueError):
        build_laptop_job(packet(repository="jweter/project-orchestrator"), workspace_root="C:/Projects")


def test_result_binds_exact_head_and_authorized_paths():
    job = build_laptop_job(packet(), workspace_root="C:/Projects")
    result = validate_laptop_result(job, {
        "job_id": job["job_id"], "packet_id": job["packet_id"], "project": "everward",
        "repository": job["repository"], "branch": job["branch"], "base_sha": SHA_A,
        "exact_head_sha": SHA_B, "status": "PASS", "verification": [{"name": "windows", "passed": True}],
        "changed_paths": ["scripts/verify_windows.ps1"], "evidence_ref": "artifact://sanitized/243",
    })
    assert result["head_sha"] == SHA_B
    assert result["merged"] is False


def test_result_rejects_unauthorized_path():
    job = build_laptop_job(packet(), workspace_root="C:/Projects")
    with pytest.raises(ValueError):
        validate_laptop_result(job, {
            "job_id": job["job_id"], "packet_id": job["packet_id"], "repository": job["repository"],
            "branch": job["branch"], "base_sha": SHA_A, "exact_head_sha": SHA_B, "status": "PASS",
            "verification": [], "changed_paths": ["Source/Everward/Secret.cpp"], "evidence_ref": "artifact://sanitized/243",
        })


def test_laptop_cannot_claim_product_reality_authority():
    job = build_laptop_job(packet(), workspace_root="C:/Projects")
    with pytest.raises(ValueError):
        validate_laptop_result(job, {
            "job_id": job["job_id"], "packet_id": job["packet_id"], "repository": job["repository"],
            "branch": job["branch"], "base_sha": SHA_A, "exact_head_sha": SHA_B, "status": "PASS",
            "verification": [], "changed_paths": [], "evidence_ref": "artifact://sanitized/243", "product_reality_verified": True,
        })


def test_idle_actor_claims_work_without_human_prompt():
    job = build_laptop_job(packet(), workspace_root="C:/Projects")
    assert next_actor_action(idle=True, active_job=None, queued_jobs=[job]) == {"action": "CLAIM", "job_id": job["job_id"]}


def test_busy_actor_waits_and_active_actor_continues():
    job = build_laptop_job(packet(), workspace_root="C:/Projects")
    assert next_actor_action(idle=False, active_job=None, queued_jobs=[job])["action"] == "WAIT_FOR_IDLE"
    assert next_actor_action(idle=False, active_job=job, queued_jobs=[])["action"] == "CONTINUE"
