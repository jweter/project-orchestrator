
from engineering.validate_builder_architecture import (
    MAX_BUILD_ONCE_LINES,
    validate,
)


def test_current_builder_stays_within_architecture_budget():
    assert validate() == []


def test_validator_rejects_build_once_regrowth(tmp_path):
    body = "\n".join(f"    x{i} = {i}" for i in range(MAX_BUILD_ONCE_LINES + 1))
    source = (
        "def promote_candidate():\n    return None\n\n"
        "def build_once():\n"
        + body
        + "\n"
    )
    path = tmp_path / "builder.py"
    path.write_text(source, encoding="utf-8")
    errors = validate(path)
    assert any("build_once orchestration budget exceeded" in error for error in errors)


def test_validator_rejects_promotion_policy_leakage(tmp_path):
    source = (
        "def promote_candidate():\n    return None\n\n"
        "def build_once():\n"
        '    status = "PR_CREATE_FAILED"\n'
        "    return status\n"
    )
    path = tmp_path / "builder.py"
    path.write_text(source, encoding="utf-8")
    errors = validate(path)
    assert any("promotion implementation leaked back" in error for error in errors)
