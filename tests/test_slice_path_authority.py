from scripts.autonomous_slice_builder import slice_path_failure

def test_slice_path_authority_allows_declared_prefix():
    authority={"allowed_paths":["src/"],"forbidden_paths":[]}
    assert slice_path_failure(["src/parser.py","src/lib/token.py"],authority) is None

def test_slice_path_authority_rejects_outside_prefix():
    authority={"allowed_paths":["src/"],"forbidden_paths":[]}
    assert slice_path_failure(["tests/test_parser.py"],authority)=="slice_path_outside_authority:tests/test_parser.py"

def test_slice_path_authority_forbidden_wins():
    authority={"allowed_paths":["src/"],"forbidden_paths":["src/secrets/"]}
    assert slice_path_failure(["src/secrets/key.py"],authority)=="slice_forbidden_path:src/secrets/key.py"

def test_slice_path_authority_rejects_traversal_and_absolute_authority():
    assert slice_path_failure(["src/a.py"],{"allowed_paths":["../src"],"forbidden_paths":[]})=="invalid_slice_path_authority"
    assert slice_path_failure(["src/a.py"],{"allowed_paths":["/src"],"forbidden_paths":[]})=="invalid_slice_path_authority"

def test_slice_path_authority_empty_allowed_preserves_repo_policy_scope():
    assert slice_path_failure(["src/a.py"],{"allowed_paths":[],"forbidden_paths":["secrets/"]}) is None
