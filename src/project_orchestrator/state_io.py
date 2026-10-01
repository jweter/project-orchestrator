from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist JSON safely across concurrent Windows readers/writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for attempt in range(5):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)
