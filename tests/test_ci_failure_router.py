from scripts.ci_failure_router import (
    CLOSED_CLASSES, SAFE_CLASSES, classify, fingerprint, first_meaningful_failure,
)

def test_extracts_first_meaningful_failure():
    log="setup ok\nE   SyntaxError: unexpected character\nFAILED later"
    assert "SyntaxError: unexpected character" in first_meaningful_failure(log)

def test_classification_is_fail_closed_for_tests_and_unknown():
    assert classify("E SyntaxError: bad token")=="TEST_FAILURE"
    assert classify("mysterious compiler event")=="UNKNOWN"
    assert "TEST_FAILURE" in CLOSED_CLASSES and "UNKNOWN" in CLOSED_CLASSES

def test_only_explicit_safe_classes_are_auto_repair_eligible():
    assert SAFE_CLASSES=={"FORMAT","IMPORT_SORT","LINT_SAFE_FIX"}
    assert classify("ruff format --check failed")=="FORMAT"
    assert classify("mypy type check failed")=="TYPE_CHECK"

def test_fingerprint_is_stable_across_line_numbers():
    a=fingerprint("o/r","abc","test","TEST_FAILURE","failed at line 13")
    b=fingerprint("o/r","abc","test","TEST_FAILURE","failed at line 99")
    assert a==b

def test_canonical_format_and_import_signatures_are_not_filtered():
    assert first_meaningful_failure("1 file would be reformatted")=="1 file would be reformatted"
    assert classify("1 file would be reformatted")=="FORMAT"
    assert classify("I001 import block is un-sorted")=="IMPORT_SORT"
