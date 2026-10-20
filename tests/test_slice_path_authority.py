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

def test_slice_path_authority_supports_recursive_glob_grants():
    authority = {"allowed_paths": ["backend/**", "src/**", "tests/**", "docs/projects/singing-mode.md"], "forbidden_paths": [".github/**", "private/**", ".env", "**/*.pem", "**/*.key"]}
    assert slice_path_failure(["backend/core/engine_schema.py", "tests/test_engine_schema.py"], authority) is None
    assert slice_path_failure(["src/voices/catalogue.py", "docs/projects/singing-mode.md"], authority) is None
    assert slice_path_failure(["unrelated/component.py"], authority) == "slice_path_outside_authority:unrelated/component.py"


def test_slice_path_authority_glob_forbidden_paths_win():
    authority = {"allowed_paths": ["backend/**", "private/**", "**/*.pem"], "forbidden_paths": ["private/**", "**/*.pem", "**/*.key"]}
    for path in ("private/secret.txt", "backend/certs/device.pem", "backend/deep/certs/private.key", "root.pem"):
        assert slice_path_failure([path], authority) == f"slice_forbidden_path:{path}"


def test_slice_path_authority_globs_are_component_anchored():
    authority = {"allowed_paths": ["backend/*"], "forbidden_paths": []}
    assert slice_path_failure(["backend/module.py"], authority) is None
    assert slice_path_failure(["backend/nested/module.py"], authority) == "slice_path_outside_authority:backend/nested/module.py"
    assert slice_path_failure(["other/backend/module.py"], authority) == "slice_path_outside_authority:other/backend/module.py"
