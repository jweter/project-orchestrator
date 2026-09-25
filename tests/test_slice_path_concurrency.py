import json
import scripts.continuous_flow_supervisor as supervisor

def test_disjoint_allowed_paths_do_not_conflict(tmp_path):
    lifecycle={"grants":{"g1":{"state":"CLAIMED","authorized_repositories":["jweter/repo"],
        "execution_slice":{"allowed_paths":["src/parser/"]}}}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(lifecycle),encoding="utf-8")
    candidate={"allowed_paths":["tests/integration/"]}
    assert supervisor.conflicting_nonterminal_grant(tmp_path,"jweter/repo",candidate) is None

def test_overlapping_allowed_paths_still_conflict(tmp_path):
    lifecycle={"grants":{"g1":{"state":"CLAIMED","authorized_repositories":["jweter/repo"],
        "execution_slice":{"allowed_paths":["src/parser/"]}}}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(lifecycle),encoding="utf-8")
    candidate={"allowed_paths":["src/parser/tokenizer.py"]}
    assert supervisor.conflicting_nonterminal_grant(tmp_path,"jweter/repo",candidate)["grant_id"]=="g1"

def test_missing_path_authority_fails_closed(tmp_path):
    lifecycle={"grants":{"g1":{"state":"ISSUED","authorized_repositories":["jweter/repo"],
        "execution_slice":{}}}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(lifecycle),encoding="utf-8")
    assert supervisor.conflicting_nonterminal_grant(
        tmp_path,"jweter/repo",{"allowed_paths":["src/"]}) is not None
