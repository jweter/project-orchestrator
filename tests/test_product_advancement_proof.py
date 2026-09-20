from scripts.continuous_flow_supervisor import record_product_advancement

def test_pr_open_is_material_advancement(tmp_path):
    proof=record_product_advancement(tmp_path,repository="repo-a",builder_status="PR_OPEN",
        lifecycle_advanced=False,burst_id="b",process_instance_id="p",sequence=1)
    assert proof["advanced_repositories"]==["repo-a"]

def test_async_wait_is_not_material_advancement(tmp_path):
    proof=record_product_advancement(tmp_path,repository="repo-a",builder_status="CI_PENDING",
        lifecycle_advanced=False,burst_id="b",process_instance_id="p",sequence=1)
    assert proof["advanced_repository_count"]==0
    assert proof["multi_product_advancement_proven"] is False

def test_two_distinct_advanced_repositories_prove_multi_product_advancement(tmp_path):
    record_product_advancement(tmp_path,repository="repo-a",builder_status="PR_OPEN",
        lifecycle_advanced=False,burst_id="b",process_instance_id="p",sequence=1)
    proof=record_product_advancement(tmp_path,repository="repo-b",builder_status="CI_PENDING",
        lifecycle_advanced=True,burst_id="b",process_instance_id="p",sequence=2)
    assert proof["advanced_repository_count"]==2
    assert proof["multi_product_advancement_proven"] is True

def test_new_burst_cannot_inherit_old_advancement(tmp_path):
    record_product_advancement(tmp_path,repository="repo-a",builder_status="MERGED",
        lifecycle_advanced=True,burst_id="old",process_instance_id="p1",sequence=1)
    proof=record_product_advancement(tmp_path,repository="repo-b",builder_status="CI_PENDING",
        lifecycle_advanced=False,burst_id="new",process_instance_id="p2",sequence=2)
    assert proof["advanced_repository_count"]==0
