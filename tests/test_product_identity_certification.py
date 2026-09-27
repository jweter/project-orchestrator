from scripts.continuous_flow_supervisor import product_for_repository, record_product_identity_advancement

def test_knowledge_engine_repositories_map_to_one_product():
    assert {product_for_repository("jweter/knowledge-engine-core"),product_for_repository("jweter/knowledge-engine-web"),
            product_for_repository("jweter/knowledge-engine-ai")}=={"knowledge-engine"}

def test_two_knowledge_engine_repos_do_not_count_as_two_products(tmp_path):
    record_product_identity_advancement(tmp_path,repository="jweter/knowledge-engine-core",builder_status="PR_OPEN",
        lifecycle_advanced=False,burst_id="b",process_instance_id="p",sequence=1)
    proof=record_product_identity_advancement(tmp_path,repository="jweter/knowledge-engine-ai",builder_status="MERGED",
        lifecycle_advanced=True,burst_id="b",process_instance_id="p",sequence=2)
    assert proof["advanced_product_count"]==1
    assert proof["all_products_proven"] is False

def test_all_four_independent_products_are_required(tmp_path):
    repos=["jweter/knowledge-engine-web","jweter/rocksmith-cdlc-generator","jweter/Project-Everward","jweter/VoiceStudio"]
    proof=None
    for sequence,repo in enumerate(repos,1):
        proof=record_product_identity_advancement(tmp_path,repository=repo,builder_status="PR_OPEN",
            lifecycle_advanced=False,burst_id="b",process_instance_id="p",sequence=sequence)
    assert proof["advanced_products"]==["everward","knowledge-engine","rocksmith","voicestudio"]
    assert proof["all_products_proven"] is True

def test_unknown_repository_cannot_satisfy_product_proof(tmp_path):
    proof=record_product_identity_advancement(tmp_path,repository="jweter/other",builder_status="MERGED",
        lifecycle_advanced=True,burst_id="b",process_instance_id="p",sequence=1)
    assert proof["advanced_product_count"]==0

def test_voicestudio_maps_to_its_own_product():
    assert product_for_repository("jweter/VoiceStudio") == "voicestudio"
