import json
from scripts.hands_off_completion_certificate import evaluate

def write(root,name,payload):
    (root/name).write_text(json.dumps(payload))

def valid(root):
    write(root,"continuation-current-burst.json",{"burst_id":"b","pass_count":3})
    write(root,"continuation-history.json",{"pass_count":3})
    write(root,"autonomous-burst-proof.json",{"burst_id":"b","multi_repository_proven":True})
    write(root,"portfolio-product-advancement-proof.json",{"burst_id":"b","all_three_products_proven":True,
        "advanced_products":["everward","knowledge-engine","rocksmith"]})
    write(root,"supervisor.json",{"burst_id":"b","process_instance_id":"p"})

def test_complete_live_evidence_passes(tmp_path):
    valid(tmp_path)
    assert evaluate(tmp_path)["status"]=="PASS"

def test_missing_product_fails_closed(tmp_path):
    valid(tmp_path)
    write(tmp_path,"portfolio-product-advancement-proof.json",{"burst_id":"b","all_three_products_proven":False,
        "advanced_products":["knowledge-engine","rocksmith"]})
    assert evaluate(tmp_path)["status"]=="FAIL"

def test_cross_burst_evidence_cannot_be_stitched(tmp_path):
    valid(tmp_path)
    write(tmp_path,"autonomous-burst-proof.json",{"burst_id":"old","multi_repository_proven":True})
    assert evaluate(tmp_path)["status"]=="FAIL"

def test_single_pass_cannot_certify(tmp_path):
    valid(tmp_path)
    write(tmp_path,"continuation-current-burst.json",{"burst_id":"b","pass_count":1})
    assert evaluate(tmp_path)["status"]=="FAIL"

def test_missing_physical_supervisor_identity_fails(tmp_path):
    valid(tmp_path)
    write(tmp_path,"supervisor.json",{"burst_id":"b"})
    assert evaluate(tmp_path)["status"]=="FAIL"
