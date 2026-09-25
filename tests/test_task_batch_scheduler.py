import scripts.continuous_flow_supervisor as supervisor

def s(sid,*paths):
    return {"slice_id":sid,"allowed_paths":list(paths)}

def test_selects_all_pairwise_disjoint_ready_tasks():
    ready=[s("parser","src/parser/"),s("ui","src/ui/"),s("tests","tests/integration/")]
    assert [x["slice_id"] for x in supervisor.select_conflict_free_slices(ready)]==["parser","ui","tests"]

def test_conflicting_task_is_skipped_but_later_safe_task_is_kept():
    ready=[s("parser","src/parser/"),s("tokenizer","src/parser/tokenizer.py"),s("ui","src/ui/")]
    assert [x["slice_id"] for x in supervisor.select_conflict_free_slices(ready)]==["parser","ui"]

def test_ambiguous_authority_fails_closed_without_starving_later_safe_task():
    ready=[s("unknown"),s("parser","src/parser/"),s("ui","src/ui/")]
    assert [x["slice_id"] for x in supervisor.select_conflict_free_slices(ready)]==["unknown"]

def test_worker_capacity_bounds_batch():
    ready=[s("a","a/"),s("b","b/"),s("c","c/")]
    assert [x["slice_id"] for x in supervisor.select_conflict_free_slices(ready,2)]==["a","b"]
