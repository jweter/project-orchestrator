from project_orchestrator.throughput_optimizer import optimize_throughput

def project(windows,merge=None,failures=None,repeats=None,repair=None):
    metrics={}
    for key,val,unit in [("merge_cycle_seconds",merge,"seconds"),("ci_failure_count",failures,"count"),("ci_repeat_failure_count",repeats,"count"),("failure_to_repair_seconds",repair,"seconds")]:
        metrics[key]={"status":"MEASURED","value":val,"unit":unit,"sample_count":1,"evidence_refs":[key]} if val is not None else {"status":"UNKNOWN"}
    return {"window_count":windows,"metrics":metrics}

def test_selects_larger_measured_bottleneck():
    snap={"by_project":{"knowledge-engine":project(4,7200,2,1),"rocksmith":project(4,1800,0,0)}}
    out=optimize_throughput(snap)
    assert out["selected_project"]=="knowledge-engine"

def test_insufficient_windows_never_select():
    snap={"by_project":{"everward":project(2,99999,9,9)}}
    assert optimize_throughput(snap)["selected_project"] is None

def test_unknown_metrics_are_omitted_not_zeroed():
    snap={"by_project":{"rocksmith":project(4,3600,None,None,None)}}
    row=optimize_throughput(snap)["projects"][0]
    assert row["state"]=="INSUFFICIENT_DATA"
    assert "ci_failure_burden" in row["unknown_components"]

def test_eligibility_filter_prevents_optimizer_override():
    snap={"by_project":{"knowledge-engine":project(4,7200,2,1),"everward":project(4,9000,5,3)}}
    out=optimize_throughput(snap,eligible_projects=["knowledge-engine"])
    assert out["selected_project"]=="knowledge-engine"
    assert len(out["projects"])==1
