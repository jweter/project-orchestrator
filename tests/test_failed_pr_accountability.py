from scripts.failed_pr_accountability import failed

def test_failed_required_check_is_owned():
    assert failed({"statusCheckRollup":[{"status":"COMPLETED","conclusion":"FAILURE"}]})

def test_pending_or_green_is_not_failure():
    assert not failed({"statusCheckRollup":[{"status":"IN_PROGRESS","conclusion":""},{"status":"COMPLETED","conclusion":"SUCCESS"}]})
