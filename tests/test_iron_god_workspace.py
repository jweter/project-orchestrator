"""Workspace path consumers agree without relocating persistent data."""
from project_orchestrator import iron_god_workspace as workspace
from project_orchestrator import iron_god_product_lane as product
from project_orchestrator import iron_god_storage_receipt as storage
from project_orchestrator import iron_god_worktree_cleanup as cleanup


def test_layout_agrees_with_existing_product_ownership(tmp_path, monkeypatch):
    state = tmp_path / "flow-state"
    expected = tmp_path / "canon-workspaces" / "products"
    lane = product.ProductLane(state)
    assert workspace.canon_workspace(state) == expected.parent
    assert lane.workspaces == workspace.product_workspace(state) == expected
    assert lane.worktrees == workspace.product_worktrees(state)
    assert lane.clone("owner/repo") == expected / "repo"
    worktree = lane.worktrees / "P1"
    worktree.mkdir(parents=True)
    marker = worktree / "preserve.txt"
    marker.write_text("unfinished", encoding="utf-8")
    monkeypatch.setattr(cleanup, "classify", lambda root, path: {"path": str(path)})
    assert cleanup.plan(state) == [{"path": str(worktree)}]
    state.mkdir()
    assert storage.record_storage_receipt(state, None)["counts"]["product_worktrees"]["count"] == 1
    assert marker.read_text(encoding="utf-8") == "unfinished"


def test_layout_is_independent_of_disposable_temp_configuration(tmp_path, monkeypatch):
    state = tmp_path / "flow-state"
    monkeypatch.setenv("IRON_GOD_TEMP_ROOT", str(tmp_path / "scratch"))
    assert workspace.product_worktrees(state) == tmp_path / "canon-workspaces" / "products" / "worktrees"
    assert not state.exists()
