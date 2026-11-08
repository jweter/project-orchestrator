"""Policy regression: owner-approved parallel scope does not grant runtime execution."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_owner_priority_reconciled_without_runtime_authority():
    instructions = (ROOT / "DoWork.txt").read_text(encoding="utf-8")
    history = (ROOT / "engineering/IRON_GOD_PRIORITY_DIRECTIVE.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "docs/OWNER_APPROVED_ROADMAP_2026-10-25.md").read_text(encoding="utf-8")
    assert instructions.startswith("PORTFOLIO DEVELOPMENT PRIORITY")
    assert "2026-10-12 Iron God-exclusive scope restriction is superseded" in instructions
    assert "Iron God is a first-class engineering workstream alongside" in instructions
    assert "authenticated current controller authorization" in instructions
    assert "not be treated as current owner direction" in history
    assert "SUPERSEDED FOR DEVELOPMENT SCOPE" in history
    assert "Iron God engine as a first-class development workstream alongside product progress" in roadmap
    assert "not itself a Take the Wheel execution grant" in roadmap


def test_historical_priority_does_not_change_dispatcher_safety():
    dispatcher = (ROOT / "src/project_orchestrator/planning_dispatch.py").read_text(encoding="utf-8")
    assert "IRON_GOD_EXCLUSIVE_NO_ENGINE_TARGET" in dispatcher
    assert 'state_root / "iron-god-exclusive.json"' in dispatcher
