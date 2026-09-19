from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .claude_dispatch import dispatch_if_warranted, select_actor


def execute_actor_selection(task: dict[str, Any], *, claude_state_dir: Path,
                            laptop_queue_dir: Path, scorecard: dict | None = None,
                            claude_opener: Callable | None = None) -> dict[str, Any]:
    decision = select_actor(task, scorecard)
    if decision["actor"] == "HUMAN":
        return {"status": "HUMAN_BOUNDARY", **decision}
    if decision["actor"] == "CLAUDE_CLOUD":
        kwargs = {"scorecard": scorecard}
        if claude_opener is not None:
            kwargs["opener"] = claude_opener
        result = dispatch_if_warranted(task, claude_state_dir, **kwargs)
        return {"status": "DISPATCHED", **result}
    required = ("repository", "task_key")
    missing = [key for key in required if not task.get(key)]
    if missing:
        raise ValueError(f"Laptop Actor task missing: {missing}")
    laptop_queue_dir.mkdir(parents=True, exist_ok=True)
    queue_path = laptop_queue_dir / f"{task['task_key']}.json"
    if queue_path.exists():
        prior = json.loads(queue_path.read_text(encoding="utf-8"))
        return {"status": "ALREADY_QUEUED", "actor": "LAPTOP_ACTOR", "queue_path": str(queue_path), "task": prior}
    envelope = {
        "actor": "LAPTOP_ACTOR", "repository": task["repository"], "task_key": task["task_key"],
        "kind": task.get("kind"), "prompt": task.get("prompt"), "queued_at": time.time(),
    }
    queue_path.write_text(json.dumps(envelope, sort_keys=True, indent=2), encoding="utf-8")
    return {"status": "QUEUED", "actor": "LAPTOP_ACTOR", "queue_path": str(queue_path), "task": envelope}
