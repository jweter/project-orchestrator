from __future__ import annotations

import os
import socket
from typing import Any

NUMERIC_FIELDS = {
    "idle_seconds": "daedalus.laptop_actor.idle_seconds",
    "duration_seconds": "daedalus.laptop_actor.cycle_duration_seconds",
    "queue_latency_seconds": "daedalus.laptop_actor.queue_latency_seconds",
    "retry_count": "daedalus.laptop_actor.retry_count",
    "backoff_seconds": "daedalus.laptop_actor.backoff_seconds",
    "memory_percent": "daedalus.laptop_actor.memory_percent",
    "disk_free_gb": "daedalus.laptop_actor.disk_free_gb",
    "job_duration_seconds": "daedalus.laptop_actor.job_duration_seconds",
}


def enabled(env: dict[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    raw = values.get("DAEDALUS_DATADOG_DOGSTATSD", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_tag(value: object) -> str:
    return str(value).replace("|", "_").replace(":", "_").replace(",", "_")


def build_packets(payload: dict[str, Any]) -> list[str]:
    state = _safe_tag(payload.get("state", "OFFLINE"))
    packets = [f"daedalus.laptop_actor.state:1|g|#state:{state}"]
    for field, metric in NUMERIC_FIELDS.items():
        value = payload.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            packets.append(f"{metric}:{value}|g")
    if payload.get("result") == "bridge_cycle_green":
        packets.append("daedalus.laptop_actor.bridge_cycle:1|c|#result:green")
    if payload.get("state") == "COOLDOWN":
        reason = _safe_tag(payload.get("reason", "failure"))
        packets.append(f"daedalus.laptop_actor.cooldown:1|c|#reason:{reason}")
    failure_class = payload.get("failure_class")
    if failure_class:
        packets.append(
            "daedalus.laptop_actor.failure:1|c|#failure_class:"
            + _safe_tag(failure_class)
        )
    return packets


def emit(
    payload: dict[str, Any], *, host: str = "127.0.0.1", port: int = 8125
) -> bool:
    if not enabled():
        return False
    data = "\n".join(build_packets(payload)).encode("utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(data, (host, port))
        return True
    except OSError:
        return False
