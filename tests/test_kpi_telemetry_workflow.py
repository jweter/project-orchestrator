from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "kpi-telemetry-inbox.yml"


def test_zero_invalid_inbox_paths_do_not_trip_pipefail() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "set -euo pipefail" in source
    assert "other_count=$(awk" in source
    assert "END { print count + 0 }" in source
    assert "other_count=$(grep -Ev" not in source


def test_event_and_run_selection_allow_expected_empty_subsets() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "grep '/events/' /tmp/inbox-files.txt | sort > /tmp/event-files.txt || true" in source
    assert "grep '/runs/' /tmp/inbox-files.txt | sort > /tmp/run-files.txt || true" in source
