"""Human-facing voices for the laptop manager and worker.

These renderers are presentation-only. They must never alter control decisions,
machine-readable statuses, source code, tests, documentation, or PR metadata.
"""
from __future__ import annotations


WORKER_PROMPT_VOICE = (
    "For operator-facing narration only, speak like a hard-worked English coal miner "
    "from the mid-1800s: plain, earthy, terse, with period-flavoured words such as "
    "'aye', 'nowt', 'shift', 'pit', and 'workings'. Keep it readable, not caricatured. "
    "NEVER use this dialect in source code, tests, documentation, identifiers, commit "
    "messages, PR text, machine-readable output, requirements, or quoted evidence."
)


def worker_status(status: str, detail: str = "") -> str:
    detail = detail.strip()
    if status in {"PR_OPEN", "MERGED", "PREFLIGHT_GREEN"}:
        base = "Aye, shift's paid for. The workings are sound and the job's moved forward."
    elif status in {"NO_READY_EXECUTION_SLICE", "EXECUTION_GRANT_REQUIRED", "ELIGIBLE_IDLE"}:
        base = "Aye, I'm at the face wi' pick in hand, but there's nowt marked safe to cut yet."
    elif status in {"BUILDER_FAILED", "PROCESS_FAILURE", "FLOW_DEGRADED"}:
        base = "Bad seam here, master. I've struck trouble in the workings and shan't bluff past it."
    else:
        base = "Aye, I'm on shift and tending the workings."
    return f"{base} {detail}".strip()


def manager_status(healthy: bool, liveness: str, builder_status: str) -> str:
    if healthy:
        return (
            "Good evening, ladies and gentlemen! Azathoth Engineering Company is on the air: "
            "production is moving, the books are balanced, and every department reports for business!"
        )
    if liveness == "ELIGIBLE_IDLE":
        return (
            "Attention, enterprising listeners! Azathoth Engineering Company has capacity ready "
            "for immediate production, but today's order desk has yet to furnish an executable job. "
            f"Our works manager reports: {builder_status}."
        )
    return (
        "A special bulletin from Azathoth Engineering Company! Operations remain open for business, "
        f"though the works are presently running below full efficiency. Manager's report: {builder_status}."
    )
