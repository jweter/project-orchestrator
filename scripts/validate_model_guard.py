"""Prove, against a real local Ollama model, that the guard interrupts the inference itself and returns the memory.

Uses a small model so the proof itself is memory-safe. Pressure is injected (the Gate's memory probe reports 0.1 GiB a few
seconds into the run); the evidence is independent of the client: Ollama's own processor time before and after the abort,
the latency of a follow-up request, and the server's list of loaded models after the unload.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
for _path in (str(_root), str(_root / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from project_orchestrator import iron_god_local_model as lm  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5-coder:1.5b")
    parser.add_argument("--trigger-after-s", type=float, default=4.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    client = lm.OllamaClient()
    started = {"at": None}
    real = lm.available_gib

    sample: dict = {}

    def available() -> float:
        # real reading until the injected pressure begins; then 0.1 GiB
        elapsed = time.monotonic() - started["at"] if started["at"] else 0.0
        if elapsed >= 2.0 and "t" not in sample:  # one CPU sample once generation is under way
            sample.update(t=elapsed, cpu=lm.inference_cpu_seconds())
        return 0.1 if started["at"] and elapsed > args.trigger_after_s else real()

    gate = lm.Gate(model_gib=1.0, reserve_gib=0.5, min_idle_s=0.0, available=available, patience=2)
    admitted, why = gate.admit()
    evidence: dict = {"model": args.model, "admitted": admitted, "admission": why, "injected_pressure_after_s": args.trigger_after_s}
    if not admitted:
        evidence["status"] = "NOT_ADMITTED"
        args.out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print("NOT_ADMITTED", why)
        return 2
    prompt = "Count from 1 to 3000. Write every number on its own line and write nothing else."
    cpu_start = lm.inference_cpu_seconds()
    started["at"] = time.monotonic()
    t0 = time.monotonic()
    cancelled_after = None
    try:
        client.generate(args.model, prompt, num_ctx=2048, num_predict=3000, keep_alive="2m", timeout=300,
                        should_abort=gate.abort_reason, poll_s=1.0)
        evidence["status"] = "COMPLETED_WITHOUT_ABORT"
    except lm.ModelCancelled as exc:
        cancelled_after = time.monotonic() - t0
        evidence.update(status="CANCELLED", reason=str(exc), cancelled_after_s=round(cancelled_after, 1),
                        seconds_after_pressure_began=round(cancelled_after - args.trigger_after_s, 1))
    if evidence["status"] == "CANCELLED":
        cpu_at_cancel = lm.inference_cpu_seconds()
        window = max(cancelled_after - sample.get("t", 0.0), 1.0)
        evidence["inference_cpu_cores_busy_during_generation"] = round((cpu_at_cancel - sample.get("cpu", cpu_start)) / window, 2)
        time.sleep(4.0)
        cpu_after = lm.inference_cpu_seconds()
        evidence["inference_cpu_cores_busy_after_cancel"] = round((cpu_after - cpu_at_cancel) / 4.0, 2)
        evidence["inference_cpu_seconds_in_the_4s_after_cancel"] = round(cpu_after - cpu_at_cancel, 2)
        t1 = time.monotonic()
        quick = client.generate(args.model, "Say OK.", num_ctx=512, num_predict=2, keep_alive="2m", timeout=120)
        evidence["follow_up_request_seconds"] = round(time.monotonic() - t1, 1)
        evidence["follow_up_reply"] = str(quick.get("response", ""))[:40]
        evidence["unloaded"] = client.unload(args.model, wait_s=40)
        evidence["loaded_models_after_unload"] = client.loaded()
        idle_cpu = evidence["inference_cpu_seconds_in_the_4s_after_cancel"]
        evidence["verdict"] = ("INFERENCE_INTERRUPTED_AND_MODEL_UNLOADED"
                               if evidence["inference_cpu_cores_busy_during_generation"] > 2.0 and evidence["inference_cpu_cores_busy_after_cancel"] < 0.5 and evidence["unloaded"] and not evidence["loaded_models_after_unload"]
                               else "NOT_PROVEN")
    args.out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0 if evidence.get("verdict") == "INFERENCE_INTERRUPTED_AND_MODEL_UNLOADED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
