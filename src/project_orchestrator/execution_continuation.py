from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
from .evolution_bridge import build_worker_handoff
from .execution_dispatch import execute_actor_selection

TERMINAL_SUCCESS={"PASS","NO_OP"}

def learn_and_continue(*, worker_result: dict[str,Any], control_plane: dict[str,Any],
                       memory: dict[str,Any], candidate_tasks: list[dict[str,Any]],
                       claude_state_dir: Path, laptop_queue_dir: Path,
                       prior_events: list[dict[str,Any]]|None=None,
                       measurements: list[dict[str,Any]]|None=None,
                       scorecard: dict|None=None, claude_opener: Callable|None=None)->dict[str,Any]:
    handoff=build_worker_handoff(worker_result=worker_result,control_plane=control_plane,memory=memory,
        prior_events=prior_events,measurements=measurements)
    learned={
        "repository":handoff["worker_result"]["repository"],
        "pattern_id":handoff["worker_result"]["pattern_id"],
        "next_action":handoff["next_action"],
        "evolution_plan":handoff["evolution_plan"],
    }
    status=handoff["worker_result"]["status"]
    if status not in TERMINAL_SUCCESS:
        return {"status":"LANE_HELD","learning":learned,"dispatch":None,
                "reason":"terminal result requires repair/evidence before lane continuation"}
    if not candidate_tasks:
        return {"status":"NO_ELIGIBLE_WORK","learning":learned,"dispatch":None}
    eligible=[t for t in candidate_tasks if t.get("repository")==handoff["worker_result"]["repository"]
              and not t.get("blocked") and not t.get("owned")]
    if not eligible:
        return {"status":"NO_ELIGIBLE_WORK","learning":learned,"dispatch":None}
    eligible.sort(key=lambda t:(int(t.get("priority",99)),str(t.get("task_key",""))))
    selected=eligible[0]
    dispatched=execute_actor_selection(selected,claude_state_dir=claude_state_dir,
        laptop_queue_dir=laptop_queue_dir,scorecard=scorecard,claude_opener=claude_opener)
    return {"status":"CONTINUED","learning":learned,"selected_task":selected,"dispatch":dispatched}
