from scripts.autonomous_slice_builder import pr_number_from_url

def test_pr_number_from_canonical_url():
    assert pr_number_from_url("https://github.com/jweter/example/pull/123\n")==123

def test_pr_number_rejects_unproven_identity():
    assert pr_number_from_url("") is None
    assert pr_number_from_url("https://github.com/jweter/example/issues/123") is None
    assert pr_number_from_url("https://github.com/jweter/example/pull/not-a-number") is None
