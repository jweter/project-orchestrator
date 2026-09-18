import pytest

from project_orchestrator.capability_compiler import (
    capture_episode, compile_capability, fingerprint, inject_capabilities,
    measure_reuse, recurring_fingerprints,
)

REPO = "jweter/knowledge-engine-core"


def episode(ref="worker:1"):
    signature = "Windows strict typing rejects POSIX-only os.killpg / signal.SIGKILL"
    return capture_episode({
        "repository": REPO, "source_ref": ref, "kind": "repair", "failure_class": "TYPE_CHECK",
        "signature": signature, "diagnosis": "POSIX process termination leaked across platform boundary",
        "action": "select process termination by platform and preserve fail-closed cleanup",
        "verification_ref": ref + ":green",
        "capability": {"type": "compatibility_rule", "mechanism": "platform-aware process termination"},
        "evidence_state": "VERIFIED",
    })


def test_capture_is_deterministic_and_sanitized():
    assert episode()["fingerprint"] == fingerprint(REPO, "TYPE_CHECK", "Windows strict typing rejects POSIX-only os.killpg / signal.SIGKILL")
    with pytest.raises(ValueError, match="secret"):
        bad = {**episode(), "token": "ghp_" + "a" * 30}
        capture_episode(bad)


def test_recurring_fingerprint_and_compile_require_repeated_verified_evidence():
    a, b = episode("worker:1"), episode("worker:2")
    assert recurring_fingerprints([a, b]) == {a["fingerprint"]: 2}
    cap = compile_capability([a, b])
    assert cap["status"] == "VERIFIED"
    assert cap["verified_episode_count"] == 2


def test_scheduler_injection_is_repository_and_fingerprint_scoped():
    a, b = episode("worker:1"), episode("worker:2")
    cap = compile_capability([a, b])
    packet = {"repository": REPO, "failure_fingerprint": a["fingerprint"], "task": "repair worker"}
    assert inject_capabilities(packet, [cap])["capabilities_to_reuse"] == [cap]
    other = {**packet, "repository": "jweter/Project-Everward"}
    assert inject_capabilities(other, [cap])["capabilities_to_reuse"] == []


def test_measured_reuse_promotes_then_demotes_on_verified_regression():
    cap = compile_capability([episode("1"), episode("2")])
    good = [{"capability_id": cap["capability_id"], "verified": True, "outcome": "PREVENTED_FAILURE"} for _ in range(3)]
    preferred = measure_reuse(cap, good)
    assert preferred["status"] == "PREFERRED"
    bad = good + [{"capability_id": cap["capability_id"], "verified": True, "outcome": "REPEATED_FAILURE"}]
    assert measure_reuse(preferred, bad)["status"] == "DEMOTED"
