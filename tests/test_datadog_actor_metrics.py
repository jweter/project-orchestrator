import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "datadog_actor_metrics.py"
SPEC = importlib.util.spec_from_file_location("datadog_actor_metrics", MODULE)
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def test_datadog_is_opt_in():
    assert metrics.enabled({}) is False
    assert metrics.enabled({"DAEDALUS_DATADOG_DOGSTATSD": "1"}) is True


def test_packets_cover_state_resources_latency_and_retries():
    packets = metrics.build_packets(
        {
            "state": "EXECUTING",
            "idle_seconds": 600,
            "queue_latency_seconds": 12.5,
            "memory_percent": 71,
            "retry_count": 2,
        }
    )
    rendered = "\n".join(packets)
    assert "daedalus.laptop_actor.state:1|g|#state:EXECUTING" in rendered
    assert "daedalus.laptop_actor.queue_latency_seconds:12.5|g" in rendered
    assert "daedalus.laptop_actor.memory_percent:71|g" in rendered
    assert "daedalus.laptop_actor.retry_count:2|g" in rendered


def test_green_cycle_and_cooldown_are_counters():
    assert (
        "daedalus.laptop_actor.bridge_cycle:1|c|#result:green"
        in metrics.build_packets(
            {"state": "IDLE_AVAILABLE", "result": "bridge_cycle_green"}
        )
    )
    packets = metrics.build_packets(
        {"state": "COOLDOWN", "reason": "resource_budget"}
    )
    assert any("daedalus.laptop_actor.cooldown:1|c" in p for p in packets)


def test_failure_class_is_sanitized():
    packets = metrics.build_packets(
        {"state": "COOLDOWN", "failure_class": "ENVIRONMENT:FAILURE"}
    )
    assert any(
        "#failure_class:ENVIRONMENT_FAILURE" in packet for packet in packets
    )
