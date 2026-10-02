from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


def atomic_json(path: Path, payload: dict[str, Any], *, replace_attempts: int = 5) -> None:
    """Persist JSON safely across concurrent Windows readers/writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        attempts = max(1, replace_attempts)
        for attempt in range(attempts):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(min(0.1 * (attempt + 1), 0.5))
    finally:
        tmp.unlink(missing_ok=True)
