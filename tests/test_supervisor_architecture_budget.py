from pathlib import Path

from engineering.validate_supervisor_architecture import (
    MAX_HELPER_LINES,
    MAX_SOURCE_LINES,
    validate,
)


def test_current_supervisor_stays_within_architecture_budget():
    assert validate() == []


def test_validator_rejects_source_line_regrowth(tmp_path):
    source = "def main():\n    return 0\n" + ("# filler\n" * (MAX_SOURCE_LINES + 1))
    path = tmp_path / "supervisor.py"
    path.write_text(source, encoding="utf-8")
    errors = validate(path)
    assert any("source budget exceeded" in error for error in errors)


def test_validator_rejects_large_non_orchestration_helper(tmp_path):
    helper_body = "\n".join(f"    x{i} = {i}" for i in range(MAX_HELPER_LINES + 1))
    source = (
        "def oversized():\n"
        + helper_body
        + "\n\ndef main():\n    return 0\n"
    )
    path = tmp_path / "supervisor.py"
    path.write_text(source, encoding="utf-8")
    errors = validate(path)
    assert any("non-orchestration helper too large" in error for error in errors)
