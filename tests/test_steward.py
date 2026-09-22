from project_orchestrator.steward import assess_machine_health, govern_dispatch


def lane(project, lane_id, resource_class="STANDARD", exclusive=None):
    return {
        "project_id": project,
        "lane_id": lane_id,
        "resource_class": resource_class,
        "exclusive_resources": exclusive or [],
    }


def test_green_allows_three_lanes():
    health = assess_machine_health(
        {
            "memory_percent": 50,
            "cpu_percent": 20,
            "temperature_c": 55,
            "on_ac_power": True,
            "disks": [{"name": "C:", "used_percent": 60, "free_gb": 150}],
        }
    )
    result = govern_dispatch(
        [
            lane("knowledge-engine", "ke"),
            lane("rocksmith", "rs"),
            lane("everward", "ev"),
        ],
        health,
    )
    assert health["band"] == "GREEN"
    assert len(result["admitted"]) == 3


def test_multivariate_orange_pressure_escalates_to_red():
    health = assess_machine_health(
        {
            "memory_percent": 92.9,
            "cpu_percent": 10,
            "on_ac_power": True,
            "disks": [{"name": "C:", "used_percent": 92.5, "free_gb": 35.5}],
        }
    )
    assert health["band"] == "RED"
    assert health["max_parallel_lanes"] == 0
    assert "MULTIVARIATE_PRESSURE_ESCALATION" in health["reasons"]


def test_orange_allows_one_nonheavy_lane():
    health = assess_machine_health(
        {
            "memory_percent": 88,
            "cpu_percent": 20,
            "on_ac_power": True,
            "disks": [{"name": "C:", "used_percent": 70, "free_gb": 100}],
        }
    )
    result = govern_dispatch(
        [
            lane("knowledge-engine", "ke", "HEAVY"),
            lane("rocksmith", "rs"),
            lane("everward", "ev"),
        ],
        health,
    )
    assert health["band"] == "ORANGE"
    assert [item["project_id"] for item in result["admitted"]] == ["rocksmith"]
    assert any(
        "RESOURCE_CLASS" in item["steward_deferred_reason"]
        for item in result["deferred"]
    )


def test_reduced_capacity_rotates_fairly():
    health = assess_machine_health(
        {
            "memory_percent": 80,
            "cpu_percent": 20,
            "on_ac_power": True,
            "disks": [{"name": "C:", "used_percent": 70, "free_gb": 100}],
        }
    )
    queue = [
        lane("knowledge-engine", "ke"),
        lane("rocksmith", "rs"),
        lane("everward", "ev"),
    ]
    result = govern_dispatch(queue, health, last_admitted_project="knowledge-engine")
    assert health["band"] == "YELLOW"
    assert [item["project_id"] for item in result["admitted"]] == [
        "rocksmith",
        "everward",
    ]
    assert result["next_fairness_cursor"] == "everward"


def test_busy_exclusive_resource_defers_matching_lane():
    health = assess_machine_health(
        {
            "memory_percent": 70,
            "cpu_percent": 20,
            "on_ac_power": True,
            "active_heavy_processes": ["ollama.exe"],
            "disks": [{"name": "C:", "used_percent": 70, "free_gb": 100}],
        }
    )
    result = govern_dispatch(
        [lane("knowledge-engine", "ke", "HEAVY", ["ollama"]), lane("rocksmith", "rs")],
        health,
    )
    assert [item["project_id"] for item in result["admitted"]] == ["rocksmith"]
    assert result["health_deferral_is_product_blocker"] is False


def test_unknown_thermal_is_evidence_gap_not_failure():
    health = assess_machine_health(
        {
            "memory_percent": 50,
            "cpu_percent": 20,
            "on_ac_power": True,
            "disks": [{"name": "C:", "used_percent": 60, "free_gb": 150}],
        }
    )
    assert health["band"] == "GREEN"
    assert "temperature_c" in health["evidence_gaps"]
