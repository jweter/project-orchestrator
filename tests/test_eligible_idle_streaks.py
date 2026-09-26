import json
from scripts.continuous_flow_supervisor import record_eligible_idle_streaks


def write_decision(root, invocation, rows):
    (root/"dispatch-decision.json").write_text(json.dumps({"invocation_id":invocation,"decisions":rows}),encoding="utf-8")


def test_two_identical_no_slice_cycles_become_p1_liveness_defect(tmp_path):
    rows=[{"repository":"jweter/knowledge-engine-core","product":"knowledge-engine",
           "head_sha":"abc","outcome":"NO_AUTHORIZED_SLICE","reason":"NO_WORK"}]
    write_decision(tmp_path,"cycle-1",rows)
    first=record_eligible_idle_streaks(tmp_path,["knowledge-engine"],current_invocation_id="cycle-1")
    assert first["status"]=="OK"
    assert first["products"]["knowledge-engine"]["streak"]==1
    write_decision(tmp_path,"cycle-2",rows)
    second=record_eligible_idle_streaks(tmp_path,["knowledge-engine"],current_invocation_id="cycle-2")
    assert second["status"]=="P1_LIVENESS_DEFECT"
    assert second["p1_liveness_defects"]==["knowledge-engine"]
    assert second["products"]["knowledge-engine"]["streak"]==2


def test_changed_head_resets_no_slice_streak(tmp_path):
    write_decision(tmp_path,"cycle-1",[{"repository":"jweter/knowledge-engine-core","product":"knowledge-engine",
        "head_sha":"old","outcome":"NO_AUTHORIZED_SLICE","reason":"NO_WORK"}])
    record_eligible_idle_streaks(tmp_path,["knowledge-engine"],current_invocation_id="cycle-1")
    write_decision(tmp_path,"cycle-2",[{"repository":"jweter/knowledge-engine-core","product":"knowledge-engine",
        "head_sha":"new","outcome":"NO_AUTHORIZED_SLICE","reason":"NO_WORK"}])
    result=record_eligible_idle_streaks(tmp_path,["knowledge-engine"],current_invocation_id="cycle-2")
    assert result["status"]=="OK"
    assert result["products"]["knowledge-engine"]["streak"]==1


def test_active_grant_clears_idle_streak(tmp_path):
    rows=[{"repository":"jweter/Project-Everward","product":"everward",
           "head_sha":"abc","outcome":"NO_AUTHORIZED_SLICE","reason":"NO_WORK"}]
    write_decision(tmp_path,"cycle-1",rows)
    record_eligible_idle_streaks(tmp_path,["everward"],current_invocation_id="cycle-1")
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{"g":{"state":"CLAIMED",
        "repository":"jweter/Project-Everward"}}}),encoding="utf-8")
    write_decision(tmp_path,"cycle-2",rows)
    result=record_eligible_idle_streaks(tmp_path,["everward"],current_invocation_id="cycle-2")
    assert result["status"]=="OK"
    assert result["products"]["everward"]["reason"]=="ACTIVE_WORK"
    assert result["products"]["everward"]["streak"]==0


def test_parallel_no_ready_slice_repeats_are_liveness_defect(tmp_path):
    for cycle in ("cycle-1","cycle-2"):
        (tmp_path/"dispatch-blocks.json").write_text(json.dumps({"invocation_id":cycle,"blocked":[{
            "product":"rocksmith","worker_id":None,"repository":None,"slice_id":None,
            "reason":"NO_CONFLICT_FREE_READY_SLICE"}]}),encoding="utf-8")
        result=record_eligible_idle_streaks(tmp_path,["rocksmith"],current_invocation_id=cycle)
    assert result["status"]=="P1_LIVENESS_DEFECT"
    assert result["products"]["rocksmith"]["streak"]==2


def test_stale_dispatch_evidence_does_not_increment_idle_streak(tmp_path):
    write_decision(tmp_path,"old",[{"repository":"jweter/knowledge-engine-core","product":"knowledge-engine",
        "head_sha":"abc","outcome":"NO_AUTHORIZED_SLICE","reason":"NO_WORK"}])
    result=record_eligible_idle_streaks(tmp_path,["knowledge-engine"],current_invocation_id="current")
    assert result["status"]=="OK"
    assert result["products"]["knowledge-engine"]["reason"]=="NO_FRESH_IDLE_EVIDENCE"
    assert result["products"]["knowledge-engine"]["streak"]==0
