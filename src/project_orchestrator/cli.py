from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from .area_learning import retrieve_learning
from .branch_lifecycle import classify_portfolio_branches
from .branch_reconciliation import build_reconciliation_plan, execute_reconciliation
from .closed_loop import validate_closed_loop_run
from .compatibility import load_contract, plan_change
from .decision import next_actions
from .evidence_collector import COLLECTIBLE_REPOSITORIES, collect_portfolio_windows
from .foreman import build_foreman_plan
from .github_evidence import UrllibGitHubTransport, collect_portfolio
from .inventory import load_inventory
from .jev_runtime import runtime_cycle
from .jev_experiment import report_from_ledger
from .learning import add_lesson, load_memory, write_memory
from .learning_effectiveness import add_application, build_report, load_store as load_learning_effectiveness_store, write_store as write_learning_effectiveness_store
from .operator_console import build_from_files as build_operator_console, render_html as render_operator_console_html
from .product_reality import aggregate_product_reality_debt
from .process_experiments import activate as activate_process_experiment, add_experiment, build_experiment, evaluate as evaluate_process_experiment, load_store as load_process_experiment_store, replace_experiment, write_store as write_process_experiment_store
from .reinvestment import plan_from_store
from .retained_learning import add_rule as add_retained_rule, load_store as load_retained_store, retain_from_experiment, write_store as write_retained_store
from .scheduler_policy import scheduler_context
from .regression_prevention import build_prevention_plan
from .repair import build_repair_plan, execute_repair_plan, load_control_plane
from .risk_predictor import build_pre_change_risk_profile
from .self_improvement import build_self_improvement_proposals
from .scheduler_report import build_scheduler_executive_report
from .specialist_lanes import build_lane_plan, validate_lane_result
from .steward import assess_machine_health
from .work_decomposition import decompose_work_item
from .required_checks import resolve_required_checks
from .throughput_optimizer import optimize_throughput
from .unattended_cycle import run_unattended_cycle
from .timeseries import (
    append_window,
    build_reinvestment_snapshot,
    load_store,
    recent_windows,
    write_store,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "engineering" / "portfolio.json"
DEFAULT_KE_COMPATIBILITY = ROOT / "engineering" / "knowledge-engine-compatibility.json"
DEFAULT_LEARNING_MEMORY = ROOT / "engineering" / "state" / "learning-memory.json"
DEFAULT_LEARNING_EFFECTIVENESS = ROOT / "engineering" / "state" / "learning-effectiveness.json"
DEFAULT_TIMESERIES_STORE = ROOT / "engineering" / "state" / "compounding-timeseries.json"
DEFAULT_PROCESS_EXPERIMENTS = ROOT / "engineering" / "state" / "process-experiments.json"
DEFAULT_RETAINED_LEARNING = ROOT / "engineering" / "state" / "retained-learning.json"
DEFAULT_JEV_LEDGER = ROOT / "telemetry" / "jev-shadow" / "decisions.jsonl"

TOOL_DELEGATES = {
    "throughput": "project_orchestrator.throughput_guard_cli",
    "evolve": "project_orchestrator.evolution_cli",
    "scientist": "project_orchestrator.scientist_cli",
    "kpi": "project_orchestrator.kpi_cli",
    "loop-audit": "project_orchestrator.loop_audit",
    "harness-audit": "project_orchestrator.harness_audit",
    "harness-security": "project_orchestrator.harness_security",
    "agent-eval": "project_orchestrator.agent_eval",
    "agent-eval-runner": "project_orchestrator.agent_eval_runner",
    "skill": "project_orchestrator.skill_extraction",
    "learning-candidate": "project_orchestrator.learning_candidates",
    "handoff-memory": "project_orchestrator.transient_memory",
}


def delegated_tool_main(tool: str, argv: list[str]) -> int:
    module_name = TOOL_DELEGATES[tool]
    module = importlib.import_module(module_name)
    return int(module.main(argv))




def validate() -> int:
    inventory = load_inventory(DEFAULT_INVENTORY)
    count = sum(len(project["repositories"]) for project in inventory["projects"])
    print(f"portfolio inventory valid: {count} repositories")
    return 0


def plan(snapshot_path: Path) -> int:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    actions = next_actions(snapshot)
    print(json.dumps([action.__dict__ for action in actions], indent=2, sort_keys=True))
    return 0


def collect(output: Path | None, resolve_checks: bool) -> int:
    transport = UrllibGitHubTransport.from_environment()
    snapshot = collect_portfolio(str(DEFAULT_INVENTORY), transport)
    if resolve_checks:
        snapshot = resolve_required_checks(snapshot, transport)
    rendered = json.dumps(snapshot, indent=2, sort_keys=True)
    if output is None:
        print(rendered)
    else:
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote raw live GitHub evidence to {output}")
    return 0


def compatibility_plan(change_path: Path) -> int:
    change = json.loads(change_path.read_text(encoding="utf-8"))
    contract = load_contract(DEFAULT_KE_COMPATIBILITY)
    print(json.dumps(plan_change(change, contract), indent=2, sort_keys=True))
    return 0


def memory_validate() -> int:
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    print(f"learning memory valid: {len(memory['entries'])} verified lessons")
    return 0


def memory_add(lesson_path: Path) -> int:
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    updated = add_lesson(memory, lesson)
    write_memory(DEFAULT_LEARNING_MEMORY, updated)
    print(f"learning memory contains {len(updated['entries'])} verified lessons")
    return 0


def learning_effectiveness_record(application_path: Path) -> int:
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    store = load_learning_effectiveness_store(DEFAULT_LEARNING_EFFECTIVENESS, memory)
    record = json.loads(application_path.read_text(encoding="utf-8"))
    updated = add_application(store, record, memory)
    write_learning_effectiveness_store(DEFAULT_LEARNING_EFFECTIVENESS, updated, memory)
    print(json.dumps(build_report(updated, memory), indent=2, sort_keys=True))
    return 0


def learning_effectiveness_report() -> int:
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    store = load_learning_effectiveness_store(DEFAULT_LEARNING_EFFECTIVENESS, memory)
    print(json.dumps(build_report(store, memory), indent=2, sort_keys=True))
    return 0


def timeseries_validate() -> int:
    store = load_store(DEFAULT_TIMESERIES_STORE)
    print(f"compounding timeseries valid: {len(store['windows'])} evidence windows")
    return 0


def timeseries_record(window_path: Path) -> int:
    window = json.loads(window_path.read_text(encoding="utf-8"))
    store = load_store(DEFAULT_TIMESERIES_STORE)
    updated = append_window(store, window)
    write_store(DEFAULT_TIMESERIES_STORE, updated)
    print(f"compounding timeseries contains {len(updated['windows'])} evidence windows")
    return 0


def timeseries_report(
    since: str | None,
    project: str | None,
    repository: str | None,
    reinvestment: bool,
) -> int:
    store = load_store(DEFAULT_TIMESERIES_STORE)
    if reinvestment:
        print(json.dumps(build_reinvestment_snapshot(store, since=since), indent=2, sort_keys=True))
        return 0
    windows = recent_windows(store, since=since, project=project, repository=repository)
    print(json.dumps(windows, indent=2, sort_keys=True))
    return 0


def reinvestment_plan(since: str | None) -> int:
    store = load_store(DEFAULT_TIMESERIES_STORE)
    result = plan_from_store(store, since=since)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def collect_evidence(repository: str | None, since: str | None, dry_run: bool) -> int:
    transport = UrllibGitHubTransport.from_environment()
    store = load_store(DEFAULT_TIMESERIES_STORE)
    repositories = [repository] if repository else None
    windows = collect_portfolio_windows(transport, store, repositories=repositories, since=since)
    if not windows:
        print("no new merged pull requests found; nothing to record")
        return 0
    updated = store
    for window in windows:
        updated = append_window(updated, window)
    if dry_run:
        print(json.dumps(windows, indent=2, sort_keys=True))
        print(f"dry run: {len(windows)} new evidence window(s) not written")
        return 0
    write_store(DEFAULT_TIMESERIES_STORE, updated)
    print(f"recorded {len(windows)} new evidence window(s); store now has {len(updated['windows'])} total")
    return 0



def repair_plan(
    repository: str,
    log_path: Path,
    control_plane_path: Path,
    iteration: int,
    execute: bool,
    repo_root: Path | None,
) -> int:
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    control_plane = load_control_plane(control_plane_path)
    result = build_repair_plan(
        repository=repository,
        log_text=log_text,
        control_plane=control_plane,
        iteration=iteration,
    )
    if execute:
        if repo_root is None:
            raise ValueError("--repo-root is required with --execute")
        result = execute_repair_plan(result, repo_root=repo_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def specialist_lane_plan(snapshot_path: Path, candidates_path: Path) -> int:
    inventory = load_inventory(DEFAULT_INVENTORY)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a JSON list")
    print(json.dumps(build_lane_plan(inventory, snapshot, candidates), indent=2, sort_keys=True))
    return 0


def specialist_lane_result(result_path: Path, live_default_head: str | None) -> int:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    print(json.dumps(validate_lane_result(result, live_default_head=live_default_head), indent=2, sort_keys=True))
    return 0


def foreman_plan(evidence_path: Path) -> int:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    inventory = load_inventory(DEFAULT_INVENTORY)
    result = build_foreman_plan(
        inventory,
        evidence["live_snapshot"],
        evidence["candidates"],
        execution_records=evidence.get("execution_records"),
        events=evidence.get("events"),
        machine_health=evidence.get("machine_health"),
        steward_state=evidence.get("steward_state"),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def jev_runtime_cycle(evidence_path: Path, ledger_path: Path) -> int:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    result = runtime_cycle(
        ledger_path,
        run_id=str(evidence["run_id"]),
        plan=evidence["foreman_plan"],
        results=evidence.get("results", []),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def unattended_cycle(evidence_path: Path, ledger_path: Path) -> int:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    inventory = load_inventory(DEFAULT_INVENTORY)
    result = run_unattended_cycle(
        inventory,
        evidence["live_snapshot"],
        evidence["candidates"],
        run_id=str(evidence["run_id"]),
        ledger_path=ledger_path,
        execution_records=evidence.get("execution_records"),
        events=evidence.get("events"),
        machine_health=evidence.get("machine_health"),
        steward_state=evidence.get("steward_state"),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def jev_experiment_report(ledger_path: Path) -> int:
    print(json.dumps(report_from_ledger(ledger_path), indent=2, sort_keys=True))
    return 0


def steward_assess(snapshot_path: Path) -> int:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    print(json.dumps(assess_machine_health(snapshot), indent=2, sort_keys=True))
    return 0


def work_decompose(item_path: Path, snapshot_path: Path | None) -> int:
    item=json.loads(item_path.read_text(encoding="utf-8"))
    snapshot=None if snapshot_path is None else json.loads(snapshot_path.read_text(encoding="utf-8"))
    print(json.dumps(decompose_work_item(item,live_snapshot=snapshot),indent=2,sort_keys=True))
    return 0

def regression_prevention(events_path: Path, policies_path: Path) -> int:
    events=json.loads(events_path.read_text(encoding="utf-8"))
    policies=json.loads(policies_path.read_text(encoding="utf-8"))
    print(json.dumps(build_prevention_plan(events,policies),indent=2,sort_keys=True))
    return 0

def throughput_optimize(since: str | None, eligible: list[str] | None) -> int:
    store=load_store(DEFAULT_TIMESERIES_STORE)
    snapshot=build_reinvestment_snapshot(store,since=since)
    print(json.dumps(optimize_throughput(snapshot,eligible_projects=eligible),indent=2,sort_keys=True))
    return 0

def closed_loop_report(evidence_path: Path) -> int:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    print(json.dumps(validate_closed_loop_run(evidence), indent=2, sort_keys=True))
    return 0


def product_reality_report(evidence_path: Path) -> int:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    print(json.dumps(aggregate_product_reality_debt(evidence), indent=2, sort_keys=True))
    return 0


def scheduler_run_report(evidence_path: Path) -> int:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    effectiveness_store = load_learning_effectiveness_store(DEFAULT_LEARNING_EFFECTIVENESS, memory)
    effectiveness = build_report(effectiveness_store, memory)
    print(json.dumps(build_scheduler_executive_report(
        project_evidence=evidence["projects"],
        results=evidence["results"],
        project_orchestrator=evidence["project_orchestrator"],
        product_repositories_inspected=evidence["product_repositories_inspected"],
        learning_evolution=evidence.get("learning_evolution"),
        learning_effectiveness=effectiveness.get("learning_effectiveness_rate"),
        jeremy_can_help=evidence.get("jeremy_can_help"),
        generated_at=evidence.get("generated_at"),
        previous_no_advancement_reasons=evidence.get("previous_no_advancement_reasons"),
    ), indent=2, sort_keys=True))
    return 0


def self_improvement_plan(events_path: Path, minimum_recurrence: int) -> int:
    events = json.loads(events_path.read_text(encoding="utf-8"))
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    store = load_store(DEFAULT_TIMESERIES_STORE)
    print(json.dumps(build_self_improvement_proposals(
        memory,
        store,
        events,
        minimum_recurrence=minimum_recurrence,
    ), indent=2, sort_keys=True))
    return 0


def process_experiment_create(proposal_path: Path, target_metrics: list[str], baseline_refs: list[str]) -> int:
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    store = load_process_experiment_store(DEFAULT_PROCESS_EXPERIMENTS)
    experiment = build_experiment(proposal, target_metrics=target_metrics, baseline_refs=baseline_refs)
    updated = add_experiment(store, experiment)
    write_process_experiment_store(DEFAULT_PROCESS_EXPERIMENTS, updated)
    print(json.dumps(experiment, indent=2, sort_keys=True))
    return 0


def process_experiment_activate(experiment_id: str, change_ref: str, verification_refs: list[str]) -> int:
    store = load_process_experiment_store(DEFAULT_PROCESS_EXPERIMENTS)
    experiment = next((x for x in store["experiments"] if x.get("experiment_id") == experiment_id), None)
    if experiment is None:
        raise ValueError(f"unknown experiment_id: {experiment_id}")
    updated_experiment = activate_process_experiment(
        experiment, change_ref=change_ref, verification_refs=verification_refs
    )
    write_process_experiment_store(
        DEFAULT_PROCESS_EXPERIMENTS, replace_experiment(store, updated_experiment)
    )
    print(json.dumps(updated_experiment, indent=2, sort_keys=True))
    return 0


def process_experiment_evaluate(
    experiment_id: str,
    metrics_path: Path,
    post_change_refs: list[str],
    safety: str,
) -> int:
    store = load_process_experiment_store(DEFAULT_PROCESS_EXPERIMENTS)
    experiment = next((x for x in store["experiments"] if x.get("experiment_id") == experiment_id), None)
    if experiment is None:
        raise ValueError(f"unknown experiment_id: {experiment_id}")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    safety_map = {"passed": True, "failed": False, "unknown": None}
    updated_experiment = evaluate_process_experiment(
        experiment,
        metric_results=metrics,
        post_change_refs=post_change_refs,
        safety_invariants_passed=safety_map[safety],
    )
    retention = updated_experiment.get("retention")
    if updated_experiment.get("state") == "KEEP" and isinstance(retention, dict):
        rule = retain_from_experiment(
            updated_experiment,
            mechanism=retention["mechanism"],
            applicability=retention["applicability"],
            scope=retention["scope"],
        )
        retained = add_retained_rule(load_retained_store(DEFAULT_RETAINED_LEARNING), rule)
        write_retained_store(DEFAULT_RETAINED_LEARNING, retained)
        updated_experiment = {**updated_experiment, "retained_rule_id": rule["rule_id"]}
    write_process_experiment_store(
        DEFAULT_PROCESS_EXPERIMENTS, replace_experiment(store, updated_experiment)
    )
    print(json.dumps(updated_experiment, indent=2, sort_keys=True))
    return 0


def process_experiment_report() -> int:
    print(json.dumps(load_process_experiment_store(DEFAULT_PROCESS_EXPERIMENTS), indent=2, sort_keys=True))
    return 0

def process_experiment_retain(experiment_id: str, mechanism: str, applicability_path: Path, scope: str) -> int:
    experiments = load_process_experiment_store(DEFAULT_PROCESS_EXPERIMENTS)
    experiment = next((item for item in experiments["experiments"] if item.get("experiment_id") == experiment_id), None)
    if experiment is None:
        raise ValueError(f"unknown experiment_id: {experiment_id}")
    applicability = json.loads(applicability_path.read_text(encoding="utf-8"))
    rule = retain_from_experiment(experiment, mechanism=mechanism, applicability=applicability, scope=scope)
    retained = add_retained_rule(load_retained_store(DEFAULT_RETAINED_LEARNING), rule)
    write_retained_store(DEFAULT_RETAINED_LEARNING, retained)
    print(json.dumps(rule, indent=2, sort_keys=True))
    return 0


def retained_policy_context(work_items_path: Path) -> int:
    work_items = json.loads(work_items_path.read_text(encoding="utf-8"))
    if not isinstance(work_items, list):
        raise ValueError("work items must be a JSON list")
    result = scheduler_context(load_retained_store(DEFAULT_RETAINED_LEARNING), work_items)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0




def operator_console(
    scheduler_report_path: Path,
    actor_telemetry_path: Path,
    output_path: Path,
    product_reality_path: Path | None = None,
) -> int:
    snapshot = build_operator_console(
        scheduler_report_path=scheduler_report_path,
        actor_telemetry_path=actor_telemetry_path,
        retained_learning_path=DEFAULT_RETAINED_LEARNING,
        process_experiments_path=DEFAULT_PROCESS_EXPERIMENTS,
        product_reality_path=product_reality_path,
    )
    output_path.write_text(render_operator_console_html(snapshot), encoding="utf-8")
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


def branch_lifecycle(evidence_path: Path) -> int:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    print(json.dumps(classify_portfolio_branches(evidence), indent=2, sort_keys=True))
    return 0


def pre_change_risk(repository: str, paths: list[str], summary: str, limit: int) -> int:
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    print(json.dumps(build_pre_change_risk_profile(
        memory,
        repository=repository,
        changed_paths=paths,
        change_summary=summary,
        limit=limit,
    ), indent=2, sort_keys=True))
    return 0


def learning_retrieve(repository: str, paths: list[str], summary: str, failure_class: str | None, limit: int) -> int:
    memory = load_memory(DEFAULT_LEARNING_MEMORY)
    print(json.dumps(retrieve_learning(
        memory,
        repository=repository,
        changed_paths=paths,
        change_summary=summary,
        failure_class=failure_class,
        limit=limit,
    ), indent=2, sort_keys=True))
    return 0


def branch_reconcile_plan(evidence_path: Path) -> int:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    print(json.dumps(build_reconciliation_plan(evidence), indent=2, sort_keys=True))
    return 0


def branch_reconcile_execute(execution_path: Path) -> int:
    payload = json.loads(execution_path.read_text(encoding="utf-8"))
    result = execute_reconciliation(
        payload["evidence"],
        repo_root=Path(payload["repo_root"]),
        modernization_branch=payload["modernization_branch"],
        unique_commits=payload["unique_commits"],
        format_commands=payload.get("format_commands"),
        targeted_test_commands=payload.get("targeted_test_commands"),
        preflight_command=payload.get("preflight_command"),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in TOOL_DELEGATES:
        return delegated_tool_main(argv[0], argv[1:])

    parser = argparse.ArgumentParser(
        prog="project-orchestrator",
        epilog="Namespaced tools: " + ", ".join(sorted(TOOL_DELEGATES)),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    unattended_parser = sub.add_parser("unattended-cycle")
    unattended_parser.add_argument("evidence", type=Path)
    unattended_parser.add_argument("--jev-ledger", type=Path, default=DEFAULT_JEV_LEDGER)
    jev_experiment_parser = sub.add_parser("jev-experiment-report")
    jev_experiment_parser.add_argument("--ledger", type=Path, default=DEFAULT_JEV_LEDGER)
    jev_runtime_parser = sub.add_parser("jev-runtime-cycle")
    jev_runtime_parser.add_argument("evidence", type=Path)
    jev_runtime_parser.add_argument("--ledger", type=Path, default=DEFAULT_JEV_LEDGER)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("snapshot", type=Path)
    compatibility_parser = sub.add_parser("compat-plan")
    compatibility_parser.add_argument("change", type=Path)
    sub.add_parser("memory-validate")
    memory_add_parser = sub.add_parser("memory-add")
    memory_add_parser.add_argument("lesson", type=Path)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--output", type=Path)
    collect_parser.add_argument(
        "--resolve-required-checks",
        action="store_true",
        help="resolve current required CI from live branch policy and control-plane evidence",
    )
    learning_effectiveness_record_parser = sub.add_parser("learning-effectiveness-record")
    learning_effectiveness_record_parser.add_argument("application", type=Path)
    sub.add_parser("learning-effectiveness-report")
    sub.add_parser("timeseries-validate")
    timeseries_record_parser = sub.add_parser("timeseries-record")
    timeseries_record_parser.add_argument("window", type=Path)
    timeseries_report_parser = sub.add_parser("timeseries-report")
    timeseries_report_parser.add_argument("--since", default=None, help="ISO-8601 cutoff; only windows ending on/after this are included")
    timeseries_report_parser.add_argument("--project", default=None)
    timeseries_report_parser.add_argument("--repository", default=None)
    timeseries_report_parser.add_argument(
        "--reinvestment",
        action="store_true",
        help="print the aggregated snapshot shape the future Reinvestment Controller will consume",
    )
    reinvestment_plan_parser = sub.add_parser("reinvestment-plan")
    reinvestment_plan_parser.add_argument("--since", default=None, help="ISO-8601 cutoff; only windows ending on/after this are considered")
    collect_evidence_parser = sub.add_parser("collect-evidence")
    collect_evidence_parser.add_argument(
        "--repository", default=None, choices=sorted(COLLECTIBLE_REPOSITORIES),
        help="collect only this repository; default is all portfolio repositories",
    )
    collect_evidence_parser.add_argument(
        "--since", default=None,
        help="ISO-8601 cutoff; default is the latest previously-collected window's end, or a 30-day lookback if none exists",
    )
    collect_evidence_parser.add_argument(
        "--dry-run", action="store_true", help="print the windows that would be recorded without writing them",
    )
    repair_parser = sub.add_parser("repair-plan")
    repair_parser.add_argument("--repository", required=True)
    repair_parser.add_argument("--log", type=Path, required=True)
    repair_parser.add_argument("--control-plane", type=Path, required=True)
    repair_parser.add_argument("--iteration", type=int, default=1)
    repair_parser.add_argument("--execute", action="store_true")
    repair_parser.add_argument("--repo-root", type=Path, default=None)
    lanes_parser = sub.add_parser("specialist-lanes")
    lanes_parser.add_argument("--snapshot", type=Path, required=True)
    lanes_parser.add_argument("--candidates", type=Path, required=True)
    lane_result_parser = sub.add_parser("specialist-result")
    lane_result_parser.add_argument("result", type=Path)
    lane_result_parser.add_argument("--live-default-head", default=None)
    foreman_parser = sub.add_parser("foreman-plan")
    foreman_parser.add_argument("evidence", type=Path)
    steward_parser = sub.add_parser("steward-assess")
    steward_parser.add_argument("snapshot", type=Path)
    throughput_parser=sub.add_parser("throughput-optimize")
    throughput_parser.add_argument("--since",default=None)
    throughput_parser.add_argument("--eligible-project",action="append",dest="eligible_projects")
    prevention_parser=sub.add_parser("regression-prevention")
    prevention_parser.add_argument("--events",type=Path,required=True)
    prevention_parser.add_argument("--policies",type=Path,required=True)
    closed_loop_parser = sub.add_parser("closed-loop-report")
    closed_loop_parser.add_argument("evidence", type=Path)
    product_reality_parser = sub.add_parser("product-reality-report")
    product_reality_parser.add_argument("evidence", type=Path)
    scheduler_report_parser = sub.add_parser("scheduler-run-report")
    scheduler_report_parser.add_argument("evidence", type=Path)
    self_parser = sub.add_parser("self-improvement-plan")
    self_parser.add_argument("events", type=Path)
    self_parser.add_argument("--minimum-recurrence", type=int, default=2)
    process_create_parser = sub.add_parser("process-experiment-create")
    process_create_parser.add_argument("proposal", type=Path)
    process_create_parser.add_argument("--target-metric", action="append", dest="target_metrics", required=True)
    process_create_parser.add_argument("--baseline-ref", action="append", dest="baseline_refs", required=True)
    process_activate_parser = sub.add_parser("process-experiment-activate")
    process_activate_parser.add_argument("experiment_id")
    process_activate_parser.add_argument("--change-ref", required=True)
    process_activate_parser.add_argument("--verification-ref", action="append", dest="verification_refs", required=True)
    process_evaluate_parser = sub.add_parser("process-experiment-evaluate")
    process_evaluate_parser.add_argument("experiment_id")
    process_evaluate_parser.add_argument("--metrics", type=Path, required=True)
    process_evaluate_parser.add_argument("--post-change-ref", action="append", dest="post_change_refs", required=True)
    process_evaluate_parser.add_argument("--safety", choices=("passed", "failed", "unknown"), required=True)
    sub.add_parser("process-experiment-report")
    retain_parser = sub.add_parser("process-experiment-retain")
    retain_parser.add_argument("experiment_id")
    retain_parser.add_argument("--mechanism", required=True)
    retain_parser.add_argument("--applicability", type=Path, required=True)
    retain_parser.add_argument("--scope", choices=("repository", "portfolio"), default="repository")
    retained_context_parser = sub.add_parser("retained-policy-context")
    retained_context_parser.add_argument("work_items", type=Path)
    console_parser = sub.add_parser("operator-console")
    console_parser.add_argument("--scheduler-report", type=Path, required=True)
    console_parser.add_argument("--actor-telemetry", type=Path, required=True)
    console_parser.add_argument("--output", type=Path, required=True)
    console_parser.add_argument("--product-reality", type=Path, default=None)
    lifecycle_parser = sub.add_parser("branch-lifecycle")
    lifecycle_parser.add_argument("evidence", type=Path)
    risk_parser = sub.add_parser("pre-change-risk")
    risk_parser.add_argument("--repository", required=True)
    risk_parser.add_argument("--path", action="append", dest="paths", required=True)
    risk_parser.add_argument("--summary", default="")
    risk_parser.add_argument("--limit", type=int, default=12)
    learning_parser = sub.add_parser("learning-retrieve")
    learning_parser.add_argument("--repository", required=True)
    learning_parser.add_argument("--path", action="append", dest="paths", required=True)
    learning_parser.add_argument("--summary", default="")
    learning_parser.add_argument("--failure-class", default=None)
    learning_parser.add_argument("--limit", type=int, default=8)
    reconcile_parser = sub.add_parser("branch-reconcile-plan")
    reconcile_parser.add_argument("evidence", type=Path)
    reconcile_execute_parser = sub.add_parser("branch-reconcile-execute")
    reconcile_execute_parser.add_argument("execution", type=Path)
    decompose_parser=sub.add_parser("decompose-work")
    decompose_parser.add_argument("item",type=Path)
    decompose_parser.add_argument("--snapshot",type=Path,default=None)
    args = parser.parse_args(argv)
    if args.command == "validate":
        return validate()
    if args.command == "unattended-cycle":
        return unattended_cycle(args.evidence, args.jev_ledger)
    if args.command == "jev-experiment-report":
        return jev_experiment_report(args.ledger)
    if args.command == "jev-runtime-cycle":
        return jev_runtime_cycle(args.evidence, args.ledger)
    if args.command == "plan":
        return plan(args.snapshot)
    if args.command == "compat-plan":
        return compatibility_plan(args.change)
    if args.command == "memory-validate":
        return memory_validate()
    if args.command == "memory-add":
        return memory_add(args.lesson)
    if args.command == "collect":
        return collect(args.output, args.resolve_required_checks)
    if args.command == "learning-effectiveness-record":
        return learning_effectiveness_record(args.application)
    if args.command == "learning-effectiveness-report":
        return learning_effectiveness_report()
    if args.command == "timeseries-validate":
        return timeseries_validate()
    if args.command == "timeseries-record":
        return timeseries_record(args.window)
    if args.command == "timeseries-report":
        return timeseries_report(args.since, args.project, args.repository, args.reinvestment)
    if args.command == "reinvestment-plan":
        return reinvestment_plan(args.since)
    if args.command == "collect-evidence":
        return collect_evidence(args.repository, args.since, args.dry_run)
    if args.command == "repair-plan":
        return repair_plan(
            args.repository,
            args.log,
            args.control_plane,
            args.iteration,
            args.execute,
            args.repo_root,
        )
    if args.command == "specialist-lanes":
        return specialist_lane_plan(args.snapshot, args.candidates)
    if args.command == "specialist-result":
        return specialist_lane_result(args.result, args.live_default_head)
    if args.command == "foreman-plan":
        return foreman_plan(args.evidence)
    if args.command == "steward-assess":
        return steward_assess(args.snapshot)
    if args.command == "throughput-optimize":
        return throughput_optimize(args.since,args.eligible_projects)
    if args.command == "regression-prevention":
        return regression_prevention(args.events,args.policies)
    if args.command == "closed-loop-report":
        return closed_loop_report(args.evidence)
    if args.command == "product-reality-report":
        return product_reality_report(args.evidence)
    if args.command == "scheduler-run-report":
        return scheduler_run_report(args.evidence)
    if args.command == "self-improvement-plan":
        return self_improvement_plan(args.events, args.minimum_recurrence)
    if args.command == "process-experiment-create":
        return process_experiment_create(args.proposal, args.target_metrics, args.baseline_refs)
    if args.command == "process-experiment-activate":
        return process_experiment_activate(args.experiment_id, args.change_ref, args.verification_refs)
    if args.command == "process-experiment-evaluate":
        return process_experiment_evaluate(args.experiment_id, args.metrics, args.post_change_refs, args.safety)
    if args.command == "process-experiment-report":
        return process_experiment_report()
    if args.command == "process-experiment-retain":
        return process_experiment_retain(args.experiment_id, args.mechanism, args.applicability, args.scope)
    if args.command == "retained-policy-context":
        return retained_policy_context(args.work_items)
    if args.command == "operator-console":
        return operator_console(
            args.scheduler_report,
            args.actor_telemetry,
            args.output,
            args.product_reality,
        )
    if args.command == "branch-lifecycle":
        return branch_lifecycle(args.evidence)
    if args.command == "pre-change-risk":
        return pre_change_risk(args.repository, args.paths, args.summary, args.limit)
    if args.command == "learning-retrieve":
        return learning_retrieve(args.repository, args.paths, args.summary, args.failure_class, args.limit)
    if args.command == "branch-reconcile-plan":
        return branch_reconcile_plan(args.evidence)
    if args.command == "branch-reconcile-execute":
        return branch_reconcile_execute(args.execution)
    if args.command == "decompose-work":
        return work_decompose(args.item,args.snapshot)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
