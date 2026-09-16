from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "engineering" / "autonomy-loop-contract.json"

LOOP_TYPES = {"FINITE", "REGULATOR", "REGULATOR_WITH_EXIT"}
FAILURE_CODES = {
    "INVALID_SCHEMA",
    "GOAL_NOT_MACHINE_DECIDABLE",
    "MISSING_VERIFICATION",
    "MISSING_ANTI_GOODHART_BOUNDARY",
    "UNBOUNDED_RETRY",
    "MISSING_RETRY_ESCALATION",
    "JUDGE_NOT_INDEPENDENT",
    "JUDGE_NOT_DETERMINISTIC",
    "BUILDER_CAN_REWRITE_ACCEPTANCE",
    "AMBIGUITY_NOT_ESCALATED",
    "STALE_CONTEXT_ALLOWED",
    "MISSING_FAIL_CLOSED_POLICY",
    "AUTHORITY_BOUNDARY_MISSING",
    "AUTHORITY_OVERLAP",
    "PRODUCT_REALITY_INFERENCE_ALLOWED",
    "REGULATOR_NO_NOOP_SEMANTICS",
    "UNCHANGED_STATE_TREATED_AS_FAILURE",
    "MISSING_EVIDENCE_PROVENANCE",
}


def load_loop_contract(path: str | Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("autonomy loop contract must be a JSON object")
    return value


def audit_loop_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Deterministically audit one autonomous engineering-loop contract.

    The audit is intentionally narrower than product policy. It verifies that
    a repeated autonomous loop has stable acceptance criteria, independent
    deterministic verification, bounded retries, explicit authority, fresh
    evidence, and safe regulator/no-op semantics. Product repositories and
    DoWork remain the governing authority for what the loop is allowed to do.
    """

    findings: list[dict[str, str]] = []

    def fail(code: str, message: str) -> None:
        if code not in FAILURE_CODES:
            raise AssertionError(f"unknown loop-audit failure code: {code}")
        findings.append({"code": code, "severity": "ERROR", "message": message})

    if contract.get("schema_version") != 1:
        fail("INVALID_SCHEMA", "schema_version must equal 1")

    loop_id = contract.get("loop_id")
    if not _nonempty(loop_id):
        fail("INVALID_SCHEMA", "loop_id must be a non-empty string")

    loop_type = contract.get("loop_type")
    if loop_type not in LOOP_TYPES:
        fail("INVALID_SCHEMA", f"loop_type must be one of {sorted(LOOP_TYPES)}")

    goal = _mapping(contract.get("goal"))
    criteria = goal.get("acceptance_criteria")
    if goal.get("machine_decidable") is not True or not _nonempty_list(criteria):
        fail(
            "GOAL_NOT_MACHINE_DECIDABLE",
            "goal must declare machine_decidable=true and non-empty acceptance_criteria",
        )

    verification = _mapping(contract.get("verification"))
    if verification.get("automated") is not True or not _nonempty_list(verification.get("evidence_sources")):
        fail(
            "MISSING_VERIFICATION",
            "verification must be automated and identify at least one evidence source",
        )

    boundaries = _mapping(contract.get("anti_goodhart"))
    if not _nonempty_list(boundaries.get("protected_invariants")):
        fail(
            "MISSING_ANTI_GOODHART_BOUNDARY",
            "anti_goodhart.protected_invariants must be non-empty",
        )
    if boundaries.get("metric_is_never_merge_authority") is not True:
        fail(
            "MISSING_ANTI_GOODHART_BOUNDARY",
            "metrics/KPIs must never be merge authority",
        )

    retries = _mapping(contract.get("retries"))
    max_attempts = retries.get("max_substantive_repair_attempts_per_blocked_item_per_run")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        fail("UNBOUNDED_RETRY", "a positive integer retry bound is required")
    if not _nonempty(retries.get("on_exhaustion")):
        fail("MISSING_RETRY_ESCALATION", "retries.on_exhaustion must define the bounded fallback")

    judge = _mapping(contract.get("judge"))
    if judge.get("independent_from_builder") is not True:
        fail("JUDGE_NOT_INDEPENDENT", "the acceptance judge must be independent from the builder")
    if judge.get("deterministic") is not True:
        fail("JUDGE_NOT_DETERMINISTIC", "the acceptance judge must be deterministic where machine-verifiable")
    if judge.get("builder_may_modify_acceptance") is not False:
        fail(
            "BUILDER_CAN_REWRITE_ACCEPTANCE",
            "the builder must not be able to rewrite acceptance criteria to make its own output pass",
        )
    if not _nonempty_list(judge.get("evidence_sources")):
        fail("MISSING_VERIFICATION", "judge.evidence_sources must be non-empty")

    ambiguity = _mapping(contract.get("ambiguity"))
    if ambiguity.get("material_ambiguity_action") != "ESCALATE_OR_DEFER":
        fail(
            "AMBIGUITY_NOT_ESCALATED",
            "material ambiguity must ESCALATE_OR_DEFER rather than be guessed",
        )

    context = _mapping(contract.get("context"))
    if context.get("fresh_live_state_required") is not True:
        fail("STALE_CONTEXT_ALLOWED", "fresh live state must be required before portfolio decisions")
    if context.get("missing_evidence_policy") != "FAIL_CLOSED":
        fail("MISSING_FAIL_CLOSED_POLICY", "missing evidence policy must be FAIL_CLOSED")
    if not _nonempty_list(context.get("authoritative_sources")):
        fail("MISSING_EVIDENCE_PROVENANCE", "context.authoritative_sources must be non-empty")

    authority = _mapping(contract.get("authority"))
    automated = authority.get("automated_actions")
    human_only = authority.get("human_only_actions")
    if not _nonempty_list(automated) or not _nonempty_list(human_only):
        fail(
            "AUTHORITY_BOUNDARY_MISSING",
            "authority must explicitly define non-empty automated_actions and human_only_actions",
        )
    elif set(automated) & set(human_only):
        fail("AUTHORITY_OVERLAP", "automated_actions and human_only_actions must be disjoint")
    if authority.get("product_reality_may_be_inferred") is not False:
        fail(
            "PRODUCT_REALITY_INFERENCE_ALLOWED",
            "Product Reality must never be inferred by the autonomous loop",
        )

    if loop_type in {"REGULATOR", "REGULATOR_WITH_EXIT"}:
        regulator = _mapping(contract.get("regulator"))
        if regulator.get("no_safe_change_action") != "NO_OP_WITH_REASON":
            fail(
                "REGULATOR_NO_NOOP_SEMANTICS",
                "a regulator must support an evidence-backed NO_OP_WITH_REASON dead-band outcome",
            )
        if regulator.get("unchanged_state_is_failure") is not False:
            fail(
                "UNCHANGED_STATE_TREATED_AS_FAILURE",
                "an unchanged healthy state must not be treated as failure by itself",
            )

    evidence = _mapping(contract.get("evidence"))
    if evidence.get("state_transitions_require_refs") is not True:
        fail(
            "MISSING_EVIDENCE_PROVENANCE",
            "state transitions must require evidence references",
        )
    if evidence.get("unknown_is_zero") is not False:
        fail(
            "MISSING_FAIL_CLOSED_POLICY",
            "UNKNOWN must remain distinct from zero/success",
        )

    return {
        "schema_version": 1,
        "loop_id": loop_id if _nonempty(loop_id) else "UNKNOWN",
        "loop_type": loop_type if loop_type in LOOP_TYPES else "UNKNOWN",
        "status": "GREEN" if not findings else "RED",
        "finding_count": len(findings),
        "findings": findings,
        "checks": {
            "machine_decidable_goal": not any(x["code"] == "GOAL_NOT_MACHINE_DECIDABLE" for x in findings),
            "independent_deterministic_judge": not any(
                x["code"] in {"JUDGE_NOT_INDEPENDENT", "JUDGE_NOT_DETERMINISTIC", "BUILDER_CAN_REWRITE_ACCEPTANCE"}
                for x in findings
            ),
            "bounded_retries": not any(
                x["code"] in {"UNBOUNDED_RETRY", "MISSING_RETRY_ESCALATION"} for x in findings
            ),
            "authority_boundary": not any(
                x["code"] in {"AUTHORITY_BOUNDARY_MISSING", "AUTHORITY_OVERLAP", "PRODUCT_REALITY_INFERENCE_ALLOWED"}
                for x in findings
            ),
            "fresh_fail_closed_context": not any(
                x["code"] in {"STALE_CONTEXT_ALLOWED", "MISSING_FAIL_CLOSED_POLICY", "MISSING_EVIDENCE_PROVENANCE"}
                for x in findings
            ),
            "anti_goodhart": not any(x["code"] == "MISSING_ANTI_GOODHART_BOUNDARY" for x in findings),
            "regulator_dead_band": not any(
                x["code"] in {"REGULATOR_NO_NOOP_SEMANTICS", "UNCHANGED_STATE_TREATED_AS_FAILURE"}
                for x in findings
            ),
        },
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty(item) for item in value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator-loop-audit")
    parser.add_argument(
        "contract",
        nargs="?",
        type=Path,
        default=DEFAULT_CONTRACT,
        help="autonomy-loop contract JSON; defaults to engineering/autonomy-loop-contract.json",
    )
    args = parser.parse_args(argv)
    result = audit_loop_contract(load_loop_contract(args.contract))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
