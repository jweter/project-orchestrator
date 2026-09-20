import json
import hashlib
from pathlib import Path
import scripts.autonomous_slice_builder as builder

def test_unique_grant_requires_issued_reservation(tmp_path):
    root=tmp_path
    grants=root/"hands-off-execution-grants"; grants.mkdir()
    repo="jweter/repo"; head="a"*40
    contract=tmp_path/"contract.json"
    contract.write_text(json.dumps({"products":{"p":[repo]}}),encoding="utf-8")
    activation=root/"hands-off-activation.json"
    activation.write_text("{}",encoding="utf-8")
    authority={"capability_id":"c","slice_id":"s","repository":repo,"exact_head":head}
    grant=grants/"g.json"
    grant.write_text(json.dumps({"schema_version":4,"grant_id":"g",
        "contract_sha256":hashlib.sha256(contract.read_bytes()).hexdigest(),
        "activation_sha256":hashlib.sha256(activation.read_bytes()).hexdigest(),
        "products":{"p":{"repositories":{repo:head}}},"execution_slice":authority}),encoding="utf-8")
    lifecycle=root/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{}}),encoding="utf-8")
    assert builder.granted_repositories(grant,tmp_path,contract,activation,lifecycle)=={}
    assert builder.claim_execution_grant(grant,repo,head,lifecycle_path=lifecycle,
        worker={"host":"h","pid":1}) is False
    assert json.loads(lifecycle.read_text())["grants"]=={}
