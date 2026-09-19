from scripts.lane_leases import LaneLease, lease_conflicts, paths_conflict

def test_exact_and_parent_child_paths_conflict():
    assert paths_conflict("src/retrieval.py","src/retrieval.py")
    assert paths_conflict("src","src/retrieval.py")

def test_independent_paths_do_not_conflict():
    assert not paths_conflict("src/retrieval.py","tests/test_cli.py")

def test_lease_conflict_detects_owned_path():
    lease=LaneLease("r","pr-1","b",("src/retrieval.py",),"2026-09-19T00:00:00+00:00")
    assert lease_conflicts(["src/retrieval.py"],[lease])
    assert not lease_conflicts(["tests/test_cli.py"],[lease])
