import json
from scripts.continuous_flow_supervisor import record_burst_repository_pass

def test_two_repositories_in_same_burst_prove_multi_repository_autonomy(tmp_path):
    record_burst_repository_pass(tmp_path,repository="repo-a",builder_status="REVIEW_BLOCKED",
        burst_id="burst-1",process_instance_id="proc-1",sequence=10)
    proof=record_burst_repository_pass(tmp_path,repository="repo-b",builder_status="PR_OPEN",
        burst_id="burst-1",process_instance_id="proc-1",sequence=11)
    assert proof["pass_count"]==2
    assert proof["distinct_repository_count"]==2
    assert proof["multi_repository_proven"] is True

def test_repeated_same_repository_does_not_overclaim(tmp_path):
    record_burst_repository_pass(tmp_path,repository="repo-a",builder_status="CI_PENDING",
        burst_id="burst-1",process_instance_id="proc-1",sequence=1)
    proof=record_burst_repository_pass(tmp_path,repository="repo-a",builder_status="PR_OPEN",
        burst_id="burst-1",process_instance_id="proc-1",sequence=2)
    assert proof["multi_repository_proven"] is False

def test_new_logical_burst_resets_burst_proof(tmp_path):
    record_burst_repository_pass(tmp_path,repository="repo-a",builder_status="PR_OPEN",
        burst_id="old",process_instance_id="proc-1",sequence=1)
    proof=record_burst_repository_pass(tmp_path,repository="repo-b",builder_status="PR_OPEN",
        burst_id="new",process_instance_id="proc-2",sequence=2)
    assert proof["burst_id"]=="new"
    assert proof["pass_count"]==1
    assert proof["distinct_repositories"]==["repo-b"]
    assert proof["multi_repository_proven"] is False
