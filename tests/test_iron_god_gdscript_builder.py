"""GDScript tasks use independent Godot scripts and never make a C++ request."""
from __future__ import annotations

import pytest

from project_orchestrator import iron_god_cpp_builder as bounded
from project_orchestrator import iron_god_gdscript_builder as godot


PROJECT = "game"


def fixture(tmp_path):
    (tmp_path / PROJECT / "tests").mkdir(parents=True)
    (tmp_path / PROJECT / "src").mkdir(parents=True)
    (tmp_path / PROJECT / "project.godot").write_text(
        "config_version=5\n", encoding="utf-8"
    )
    (tmp_path / PROJECT / "tests" / "test_movement.gd").write_text(
        "extends SceneTree\nfunc _initialize():\n    quit(0)\n", encoding="utf-8"
    )
    return {
        "id": "everward-godot-probe-movement",
        "repository": "jweter/Project-Everward",
        "owner_approved": True,
        "capability_level": 1,
        "route": {"builder": "LOCAL"},
        "gdscript": {
            "project_root": PROJECT,
            "module": PROJECT + "/src/movement.gd",
            "test_script": PROJECT + "/tests/test_movement.gd",
        },
        "allowed_paths": [PROJECT + "/src/movement.gd"],
        "slice_acceptance": ["Existing headless test script exits with 0"],
        "objective": "Implement the already-approved probe movement behavior",
    }


def test_prepares_gdscript_headless_parse_and_existing_test(tmp_path):
    request = fixture(tmp_path)
    task = godot.make_task(request, tmp_path, which=lambda key: "/opt/godot" if key == "godot" else None)
    assert task.language == "GDScript"
    assert task.files == ["game/src/movement.gd"]
    assert task.allowed_paths == ["game/src/movement.gd"]
    assert task.test == ["/opt/godot", "--headless", "--path", str(tmp_path / "game"),
                         "--script", "tests/test_movement.gd"]
    assert task.build[-3:] == ["--script", "src/movement.gd", "--check-only"]
    assert "GDScript engineer" in bounded.initial_prompt(task, bounded.Limits())
    assert "C++ engineer" not in bounded.initial_prompt(task, bounded.Limits())
    assert "C++ engineer" in bounded.initial_prompt(
        bounded.Task("native-legacy", "legacy", tmp_path, ["src/**"]),
        bounded.Limits(),
    )


def test_owner_scope_test_or_toolchain_missing_is_fail_closed(tmp_path):
    request = fixture(tmp_path)
    assert not godot.validate(request, tmp_path)
    assert "OWNER_APPROVAL_REQUIRED" in godot.validate(
        {**request, "owner_approved": False}, tmp_path
    )
    assert "LOCAL_ROUTE_REQUIRED" in godot.validate(
        {**request, "route": {"builder": "CAPABLE"}}, tmp_path
    )
    assert "MEASURED_LEVEL_ONE_REQUIRED" in godot.validate(
        {**request, "capability_level": 5}, tmp_path
    )
    assert "ONLY_DECLARED_GDSCRIPT_MODULE_EDITABLE" in godot.validate(
        {**request, "allowed_paths": ["game/**"]}, tmp_path
    )
    spec = request["gdscript"]
    assert "TEST_MUST_BE_INDEPENDENT" in godot.validate(
        {**request, "gdscript": {**spec, "test_script": spec["module"]}}, tmp_path
    )
    assert "NONCANONICAL_GODOT_PATH" in godot.validate(
        {**request, "gdscript": {**spec, "module": "game/../src/movement.gd"}}, tmp_path
    )
    assert "INDEPENDENT_ACCEPTANCE_MISSING" in godot.validate(
        {**request, "slice_acceptance": []}, tmp_path
    )
    assert godot.toolchain_problems(lambda _: None) == ["GODOT_EXECUTABLE_UNAVAILABLE"]
    with pytest.raises(ValueError, match="GODOT_EXECUTABLE_UNAVAILABLE"):
        godot.make_task(request, tmp_path, which=lambda _: None)


def test_test_entry_must_exit_and_be_existing_independent_oracle(tmp_path):
    request = fixture(tmp_path)
    check = tmp_path / "game/tests/test_movement.gd"
    check.write_text("extends Node\nfunc run():\n    assert(true)\n")
    assert "TEST_MUST_BE_EXECUTABLE_SCENETREE" in godot.validate(request, tmp_path)
    check.unlink()
    assert "APPROVED_TEST_SCRIPT_MISSING" in godot.validate(request, tmp_path)


def test_generic_local_edit_loop_preserves_test_script(tmp_path):
    request = fixture(tmp_path)
    fixed_test = (tmp_path / "game/tests/test_movement.gd").read_bytes()
    task = godot.make_task(request, tmp_path, which=lambda _: "godot")
    commands = []

    def run(args, cwd, timeout):
        commands.append(args)
        return 0, "OK"

    def ask(prompt):
        assert "GDScript engineer" in prompt
        return "=== FILE: game/src/movement.gd ===\nextends RefCounted\nfunc step() -> int:\n    return 1\n=== END ===\n"

    outcome = bounded.build_change(
        task, ask, run=run,
        limits=bounded.Limits(max_iterations=1, memory_waits=0),
        admit=lambda: (True, "test-admitted"),
    )
    assert outcome["status"] == bounded.SUCCESS
    assert commands[0][-1] == "--check-only"
    assert commands[1][-2:] == ["--script", "tests/test_movement.gd"]
    assert (tmp_path / "game/tests/test_movement.gd").read_bytes() == fixed_test
    assert (tmp_path / "game/src/movement.gd").exists()


def test_local_loop_rejects_attempted_test_modification(tmp_path):
    request = fixture(tmp_path)
    task = godot.make_task(request, tmp_path, which=lambda _: "godot")
    fixed_test = (tmp_path / "game/tests/test_movement.gd").read_bytes()

    outcome = bounded.build_change(
        task, lambda _: (
            "=== FILE: game/src/movement.gd ===\nextends RefCounted\n"
            "=== FILE: game/tests/test_movement.gd ===\nextends SceneTree\nquit(0)\n=== END ==="
        ),
        run=lambda *args: (0, "OK"),
        limits=bounded.Limits(max_iterations=1, memory_waits=0),
        admit=lambda: (True, "test-admitted"),
    )
    assert outcome["status"] == "ITERATIONS_EXHAUSTED"
    assert not (tmp_path / "game/src/movement.gd").exists()
    assert (tmp_path / "game/tests/test_movement.gd").read_bytes() == fixed_test
