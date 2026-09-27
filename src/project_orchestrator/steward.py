from __future__ import annotations

from typing import Any

STEWARD_NAME = "Steward"
BANDS = ("GREEN", "YELLOW", "ORANGE", "RED", "CRITICAL")
BAND_RANK = {band: index for index, band in enumerate(BANDS)}
PROJECT_ORDER = ("knowledge-engine", "rocksmith", "everward", "voicestudio")

DEFAULT_POLICY: dict[str, Any] = {
    "memory_percent": {"YELLOW": 70.0, "ORANGE": 85.0, "RED": 95.0, "CRITICAL": 98.0},
    "memory_available_gb": {"YELLOW": 4.0, "ORANGE": 2.5, "RED": 1.5, "CRITICAL": 0.75},
    "disk_used_percent": {
        "YELLOW": 80.0,
        "ORANGE": 90.0,
        "RED": 95.0,
        "CRITICAL": 98.0,
    },
    "disk_free_gb": {"YELLOW": 80.0, "ORANGE": 50.0, "RED": 20.0, "CRITICAL": 10.0},
    "cpu_percent": {"YELLOW": 75.0, "ORANGE": 90.0, "RED": 97.0, "CRITICAL": 99.5},
    "temperature_c": {"YELLOW": 78.0, "ORANGE": 85.0, "RED": 90.0, "CRITICAL": 95.0},
    "battery_percent": {"YELLOW": 50.0, "ORANGE": 30.0, "RED": 15.0, "CRITICAL": 5.0},
    "multivariate_escalation_dimensions": 2,
}


def _high_band(value: float, thresholds: dict[str, float]) -> str:
    band = "GREEN"
    for candidate in ("YELLOW", "ORANGE", "RED", "CRITICAL"):
        if value >= float(thresholds[candidate]):
            band = candidate
    return band


def _low_band(value: float, thresholds: dict[str, float]) -> str:
    for candidate in ("CRITICAL", "RED", "ORANGE", "YELLOW"):
        if value < float(thresholds[candidate]):
            return candidate
    return "GREEN"


def _worst(left: str, right: str) -> str:
    return left if BAND_RANK[left] >= BAND_RANK[right] else right


def _escalate(band: str) -> str:
    return BANDS[min(len(BANDS) - 1, BAND_RANK[band] + 1)]


def _number(snapshot: dict[str, Any], key: str) -> float | None:
    value = snapshot.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _disk_dimensions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    disks = snapshot.get("disks")
    if isinstance(disks, list):
        return [item for item in disks if isinstance(item, dict)]
    fallback: dict[str, Any] = {}
    for key in ("disk_used_percent", "disk_free_gb", "disk_total_gb"):
        if key in snapshot:
            fallback[key] = snapshot[key]
    return [fallback] if fallback else []


def _capacity_for_band(band: str) -> dict[str, Any]:
    if band == "GREEN":
        return {
            "max_parallel_lanes": 3,
            "allowed_resource_classes": ["LIGHT", "STANDARD", "HEAVY"],
        }
    if band == "YELLOW":
        return {
            "max_parallel_lanes": 2,
            "allowed_resource_classes": ["LIGHT", "STANDARD", "HEAVY"],
        }
    if band == "ORANGE":
        return {
            "max_parallel_lanes": 1,
            "allowed_resource_classes": ["LIGHT", "STANDARD"],
        }
    return {"max_parallel_lanes": 0, "allowed_resource_classes": []}


def _recovery_actions(band: str) -> list[str]:
    if band == "GREEN":
        return []
    if band == "YELLOW":
        return ["reduce_concurrency", "avoid_unnecessary_heavy_overlap"]
    if band == "ORANGE":
        return [
            "limit_to_one_lane",
            "defer_heavy_work",
            "release_idle_models_and_build_tools",
            "perform_safe_cache_and_temp_cleanup",
        ]
    if band == "RED":
        return [
            "stop_new_engineering_jobs",
            "persist_execution_state",
            "reclaim_memory_and_disk_safely",
            "preserve_workspaces",
            "surface_machine_health_alert",
        ]
    return [
        "fail_closed",
        "stop_new_engineering_jobs",
        "persist_execution_state",
        "protect_workspaces_and_data",
        "surface_machine_health_alert",
    ]


def assess_machine_health(
    snapshot: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify sustainable execution capacity without inventing missing telemetry."""
    cfg = DEFAULT_POLICY if policy is None else {**DEFAULT_POLICY, **policy}
    dimensions: list[dict[str, Any]] = []
    evidence_gaps: list[str] = []

    memory = _number(snapshot, "memory_percent")
    available_memory = _number(snapshot, "memory_available_gb")
    memory_band = "GREEN"
    memory_signals: list[dict[str, Any]] = []
    if memory is None:
        evidence_gaps.append("memory_percent")
    else:
        percent_band = _high_band(memory, cfg["memory_percent"])
        memory_band = _worst(memory_band, percent_band)
        memory_signals.append(
            {"metric": "memory_percent", "value": memory, "band": percent_band}
        )
    if available_memory is not None:
        available_band = _low_band(available_memory, cfg["memory_available_gb"])
        memory_band = _worst(memory_band, available_band)
        memory_signals.append(
            {
                "metric": "memory_available_gb",
                "value": available_memory,
                "band": available_band,
            }
        )
    if memory_signals:
        dimensions.append(
            {"dimension": "memory", "band": memory_band, "signals": memory_signals}
        )

    cpu = _number(snapshot, "cpu_percent")
    if cpu is None:
        evidence_gaps.append("cpu_percent")
    else:
        dimensions.append(
            {
                "dimension": "cpu",
                "value": cpu,
                "band": _high_band(cpu, cfg["cpu_percent"]),
            }
        )

    temperature = _number(snapshot, "temperature_c")
    if temperature is None:
        evidence_gaps.append("temperature_c")
    else:
        dimensions.append(
            {
                "dimension": "thermal",
                "value": temperature,
                "band": _high_band(temperature, cfg["temperature_c"]),
            }
        )
    storage_band = "GREEN"
    volumes: list[dict[str, Any]] = []
    for index, disk in enumerate(_disk_dimensions(snapshot)):
        name = str(disk.get("name") or disk.get("path") or f"disk-{index}")
        used = disk.get("used_percent")
        if used is None:
            used = disk.get("disk_used_percent")
        free = disk.get("free_gb")
        if free is None:
            free = disk.get("disk_free_gb")
        disk_band = "GREEN"
        signals = []
        if isinstance(used, (int, float)) and not isinstance(used, bool):
            used_band = _high_band(float(used), cfg["disk_used_percent"])
            disk_band = _worst(disk_band, used_band)
            signals.append(
                {"metric": "used_percent", "value": float(used), "band": used_band}
            )
        if isinstance(free, (int, float)) and not isinstance(free, bool):
            free_band = _low_band(float(free), cfg["disk_free_gb"])
            disk_band = _worst(disk_band, free_band)
            signals.append(
                {"metric": "free_gb", "value": float(free), "band": free_band}
            )
        if signals:
            storage_band = _worst(storage_band, disk_band)
            volumes.append({"name": name, "band": disk_band, "signals": signals})
    if volumes:
        dimensions.append(
            {"dimension": "storage", "band": storage_band, "volumes": volumes}
        )

    on_ac = snapshot.get("on_ac_power")
    battery = _number(snapshot, "battery_percent")
    if on_ac is False and battery is not None:
        dimensions.append(
            {
                "dimension": "battery",
                "value": battery,
                "band": _low_band(battery, cfg["battery_percent"]),
            }
        )
    elif on_ac is None:
        evidence_gaps.append("power_source")

    overall = "GREEN"
    for item in dimensions:
        overall = _worst(overall, str(item["band"]))
    pressure_count = sum(
        BAND_RANK[str(item["band"])] >= BAND_RANK["ORANGE"] for item in dimensions
    )
    multivariate_escalation = pressure_count >= int(
        cfg["multivariate_escalation_dimensions"]
    ) and overall in {"YELLOW", "ORANGE"}
    if multivariate_escalation:
        overall = _escalate(overall)

    heavy = snapshot.get("active_heavy_processes", [])
    if not isinstance(heavy, list):
        heavy = []
    busy_exclusive = sorted(
        {
            name
            for name in ("ollama", "unreal", "aider")
            if any(name in str(process).lower() for process in heavy)
        }
    )
    capacity = _capacity_for_band(overall)

    reasons = [
        f"{item['dimension']}={item['band']}"
        for item in dimensions
        if item["band"] != "GREEN"
    ]
    if multivariate_escalation:
        reasons.append("MULTIVARIATE_PRESSURE_ESCALATION")

    return {
        "schema_version": 1,
        "governor": STEWARD_NAME,
        "band": overall,
        "allow_new_engineering": capacity["max_parallel_lanes"] > 0,
        **capacity,
        "reasons": reasons,
        "dimensions": dimensions,
        "busy_exclusive_resources": busy_exclusive,
        "recovery_actions": _recovery_actions(overall),
        "evidence_gaps": sorted(set(evidence_gaps)),
        "snapshot": snapshot,
        "policy_semantics": "maximize sustained verified advancement inside a safe machine operating envelope",
    }


def _resource_class(lane: dict[str, Any]) -> str:
    raw = str(lane.get("resource_class") or "STANDARD").upper()
    return raw if raw in {"LIGHT", "STANDARD", "HEAVY"} else "STANDARD"


def _rotated_projects(last_project: str | None) -> list[str]:
    order = list(PROJECT_ORDER)
    if last_project not in order:
        return order
    index = order.index(last_project)
    return order[index + 1 :] + order[: index + 1]


def govern_dispatch(
    queue: list[dict[str, Any]],
    assessment: dict[str, Any],
    *,
    last_admitted_project: str | None = None,
) -> dict[str, Any]:
    """Admit bounded lanes fairly; health deferral never becomes a product blocker."""
    capacity = int(assessment.get("max_parallel_lanes", 0))
    allowed = set(assessment.get("allowed_resource_classes", []))
    busy = set(assessment.get("busy_exclusive_resources", []))
    ordered_projects = _rotated_projects(last_admitted_project)
    rank = {project: index for index, project in enumerate(ordered_projects)}
    ordered = sorted(
        queue,
        key=lambda lane: (
            rank.get(lane.get("project_id"), 99),
            lane.get("lane_id", ""),
        ),
    )

    admitted: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for lane in ordered:
        resource_class = _resource_class(lane)
        exclusive = {
            str(item).lower()
            for item in lane.get("exclusive_resources", [])
            if isinstance(item, str)
        }
        if len(admitted) >= capacity:
            reason = "STEWARD_CONCURRENCY_CAP"
        elif resource_class not in allowed:
            reason = f"STEWARD_RESOURCE_CLASS_DEFERRED:{resource_class}"
        elif exclusive & busy:
            reason = "STEWARD_EXCLUSIVE_RESOURCE_BUSY:" + ",".join(
                sorted(exclusive & busy)
            )
        else:
            admitted.append(lane)
            continue
        deferred.append({**lane, "steward_deferred_reason": reason})

    cursor = admitted[-1].get("project_id") if admitted else last_admitted_project
    return {
        "assessment": assessment,
        "admitted": admitted,
        "deferred": deferred,
        "next_fairness_cursor": cursor,
        "health_deferral_is_product_blocker": False,
    }

\n