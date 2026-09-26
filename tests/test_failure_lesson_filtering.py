import json
import scripts.autonomous_slice_builder as builder

def test_capacity_deferral_is_not_injected_as_engineering_experience(tmp_path):
    state=tmp_path/"builder.json"
    lessons=tmp_path/"autonomous-failure-lessons.json"
    lessons.write_text(json.dumps({"lessons":{
        "infra":{"occurrences":4,"last_outcome":"RUNNER_CAPACITY_DEFERRED",
                 "last_seen":"2026-09-26T00:00:00+00:00","detail":"session limit"},
        "code":{"occurrences":2,"last_outcome":"BUILDER_FAILED",
                "last_seen":"2026-09-25T00:00:00+00:00","detail":"real code failure"}
    }}),encoding="utf-8")
    experience=builder.prior_failure_experience(state)
    assert "session limit" not in experience
    assert "real code failure" in experience

def test_capacity_deferral_alone_yields_no_prior_engineering_experience(tmp_path):
    state=tmp_path/"builder.json"
    lessons=tmp_path/"autonomous-failure-lessons.json"
    lessons.write_text(json.dumps({"lessons":{
        "infra":{"occurrences":4,"last_outcome":"RUNNER_CAPACITY_DEFERRED",
                 "last_seen":"2026-09-26T00:00:00+00:00","detail":"usage limit"}
    }}),encoding="utf-8")
    assert builder.prior_failure_experience(state)=="none recorded"
