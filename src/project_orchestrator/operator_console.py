from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .actor_metrics import read_events, summarize


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_snapshot(
    *,
    scheduler_report: dict[str, Any],
    actor_events: list[dict[str, Any]],
    retained_learning: dict[str, Any],
    process_experiments: dict[str, Any],
    product_reality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = summarize(actor_events)
    experiments = process_experiments.get("experiments", [])
    rules = retained_learning.get("rules", [])
    result = {
        "schema_version": 1,
        "execution": {
            "actor": actor,
            "current_scheduler_status": scheduler_report.get("executive_status", "UNKNOWN"),
            "management_bottom_line": scheduler_report.get("management_bottom_line", "UNKNOWN"),
        },
        "learning": {
            "retained_rule_count": len(rules),
            "retained_rule_ids": [item.get("rule_id") for item in rules],
            "process_experiment_states": {
                state: sum(1 for item in experiments if item.get("state") == state)
                for state in sorted({item.get("state", "UNKNOWN") for item in experiments})
            },
        },
        "advancement": scheduler_report.get("kpis", {}),
        "failures": {
            "no_advancement_reasons": scheduler_report.get("no_advancement_reasons", {}),
            "blocked": scheduler_report.get("results", {}).get("BLOCKED", []),
        },
        "verification_debt": {
            "waiting_on_preflight": scheduler_report.get("results", {}).get("WAITING_ON_PREFLIGHT", []),
            "waiting_on_ci": scheduler_report.get("results", {}).get("WAITING_ON_CI", []),
        },
        "product_reality": product_reality or {"status": "UNKNOWN"},
        "decision_basis": {
            "project_orchestrator": scheduler_report.get("project_orchestrator", {}),
            "learning_evolution": scheduler_report.get("learning_evolution", {}),
            "jeremy_can_help": scheduler_report.get("jeremy_can_help", []),
        },
    }
    return result


def build_from_files(
    *,
    scheduler_report_path: Path,
    actor_telemetry_path: Path,
    retained_learning_path: Path,
    process_experiments_path: Path,
    product_reality_path: Path | None = None,
) -> dict[str, Any]:
    product_reality = _load_json(product_reality_path) if product_reality_path else None
    return build_snapshot(
        scheduler_report=_load_json(scheduler_report_path),
        actor_events=read_events(actor_telemetry_path),
        retained_learning=_load_json(retained_learning_path),
        process_experiments=_load_json(process_experiments_path),
        product_reality=product_reality,
    )


def render_html(snapshot: dict[str, Any]) -> str:
    payload = html.escape(json.dumps(snapshot, indent=2, sort_keys=True))
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Daedalus Operator Console</title>"
        "<style>body{font-family:system-ui;margin:2rem;max-width:1100px}"
        "pre{white-space:pre-wrap;background:#111;color:#eee;padding:1rem;border-radius:8px}"
        "h1{margin-bottom:.25rem}</style></head><body>"
        "<h1>Daedalus Operator Console</h1>"
        "<p>Evidence-backed execution, learning, failures, debt, and decision state.</p>"
        f"<pre>{payload}</pre></body></html>"
    )
