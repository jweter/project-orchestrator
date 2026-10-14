from __future__ import annotations

import pytest

from project_orchestrator.iron_god_verification_receipt import (
    GREEN,
    RED,
    GateResult,
    ReceiptValidationError,
    build_receipt,
    validate_receipt,
)


def _raw_gate(
    name: str,
    *,
    returncode: int = 0,
    timed_out: bool = False,
    interrupted: bool = False,
) -> dict:
    return {
        "name": name,
        "returncode": returncode,
        "timed_out": timed_out,
        "interrupted": interrupted,
    }


def _receipt_payload(gates: list[dict], **overrides) -> dict:
    gate_results = tuple(
        GateResult(g["name"], g["returncode"], g["timed_out"], g["interrupted"])
        for g in gates
    )
    receipt = build_receipt("a" * 40, "demo-repo", gate_results)
    payload = {
        "head_sha": receipt.head_sha,
        "repository": receipt.repository,
        "gates": gates,
        "content_hash": receipt.content_hash,
    }
    payload.update(overrides)
    return payload


def test_gate_result_passed_true_only_when_fully_clean():
    assert GateResult("x", 0, False, False).passed is True
    assert GateResult("x", 1, False, False).passed is False
    assert GateResult("x", 0, True, False).passed is False
    assert GateResult("x", 0, False, True).passed is False


def test_build_and_validate_round_trip_green():
    payload = _receipt_payload([_raw_gate("a"), _raw_gate("b")])
    receipt = validate_receipt(payload)
    assert receipt.status == GREEN
    assert receipt.head_sha == "a" * 40


def test_status_red_when_any_gate_failed():
    payload = _receipt_payload([_raw_gate("a"), _raw_gate("b", returncode=1)])
    receipt = validate_receipt(payload)
    assert receipt.status == RED


@pytest.mark.parametrize("missing", ["head_sha", "repository", "gates", "content_hash"])
def test_validate_rejects_missing_required_field(missing):
    payload = _receipt_payload([_raw_gate("a")])
    del payload[missing]
    with pytest.raises(ReceiptValidationError):
        validate_receipt(payload)


def test_validate_rejects_empty_gates_list():
    payload = _receipt_payload([_raw_gate("a")])
    payload["gates"] = []
    with pytest.raises(ReceiptValidationError):
        validate_receipt(payload)


@pytest.mark.parametrize(
    "missing_field", ["name", "returncode", "timed_out", "interrupted"]
)
def test_validate_rejects_gate_missing_field(missing_field):
    payload = _receipt_payload([_raw_gate("a")])
    del payload["gates"][0][missing_field]
    with pytest.raises(ReceiptValidationError):
        validate_receipt(payload)


def test_validate_rejects_duplicate_gate_names():
    payload = _receipt_payload([_raw_gate("a"), _raw_gate("a")])
    # Recompute hash is irrelevant here -- duplicate detection must fire
    # before (or regardless of) the hash check.
    with pytest.raises(ReceiptValidationError):
        validate_receipt(payload)


def test_validate_detects_tampered_gate_result():
    payload = _receipt_payload([_raw_gate("a"), _raw_gate("b")])
    # Flip a result after the hash was computed, without recomputing it --
    # this is exactly the tamper scenario the content_hash exists to catch.
    payload["gates"][1]["returncode"] = 1
    with pytest.raises(ReceiptValidationError):
        validate_receipt(payload)


def test_validate_detects_tampered_content_hash_field():
    payload = _receipt_payload([_raw_gate("a")])
    payload["content_hash"] = "0" * 64
    with pytest.raises(ReceiptValidationError):
        validate_receipt(payload)


def test_validate_detects_tampered_head_sha():
    payload = _receipt_payload([_raw_gate("a")])
    payload["head_sha"] = "b" * 40  # does not match the hash computed for "a"*40
    with pytest.raises(ReceiptValidationError):
        validate_receipt(payload)
