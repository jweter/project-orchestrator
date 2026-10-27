"""Independent tests of the isolated benchmark and strict patch boundary."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engineering" / "free_builder_benchmark"))
from benchmark import (  # noqa: E402
    candidate_executable,
    apply_proposal,
    prepare,
    safe_relative,
    verify,
    local_ollama_proposal,
    BASELINE,
)


GOOD = """import math


def snap_beats(seconds, bpm):
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("bpm must be positive and finite")
    step = 60.0 / bpm
    result = []
    for value in seconds:
        if not math.isfinite(value):
            raise ValueError("time must be finite")
        result.append(math.floor(max(0.0, value) / step + 0.5) * step)
    return result
"""


class TestBenchmark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "task"
        prepare(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_baseline_is_not_accepted(self):
        result = verify(self.root)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["changed_files"], [])

    def test_good_patch_passes_independent_tests(self):
        apply_proposal(self.root, json.dumps({"files": {"src/tempo.py": GOOD}}))
        result = verify(self.root)
        self.assertTrue(result["accepted"], result)
        self.assertTrue(result["tests_passed"])

    def test_unapproved_file_rejected_before_write(self):
        with self.assertRaises(ValueError):
            apply_proposal(
                self.root,
                json.dumps(
                    {"files": {"src/tempo.py": GOOD, "tests/test_tempo.py": "pass"}}
                ),
            )
        self.assertEqual(
            (self.root / "src" / "tempo.py").read_text(encoding="utf-8"), BASELINE
        )

    def test_traversal_rejected(self):
        for value in (
            "../escape.py",
            "/etc/passwd",
            r"..\escape.py",
            ".git/config",
            "src/other.py",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative(value)

    def test_bad_behavior_fails_tests(self):
        apply_proposal(
            self.root,
            json.dumps({"files": {"src/tempo.py": "def snap_beats(a, b): return a\n"}}),
        )
        self.assertFalse(verify(self.root)["accepted"])

    def test_repeat_verification_does_not_create_untracked_bytecode(self):
        apply_proposal(self.root, json.dumps({"files": {"src/tempo.py": GOOD}}))
        self.assertTrue(verify(self.root)["accepted"])
        self.assertTrue(verify(self.root)["accepted"])

    @patch("benchmark.live_supervisor", return_value=True)
    def test_local_inference_refuses_live_supervisor(self, _running):
        with self.assertRaisesRegex(RuntimeError, "supervisor is active"):
            local_ollama_proposal("qwen2.5-coder:7b")

    @patch("benchmark.live_supervisor", return_value=False)
    @patch("benchmark.memory_gib", return_value=2.1)
    def test_local_inference_refuses_low_memory(self, _memory, _running):
        with self.assertRaisesRegex(RuntimeError, "3-GiB"):
            local_ollama_proposal("qwen2.5-coder:7b")

    @patch("benchmark.shutil.which", return_value=None)
    def test_isolated_tool_executable_discovered_without_path_changes(self, _which):
        local = Path(self.tmp.name) / "local"
        cli = (
            local
            / "IronGodBuilderBench"
            / "tools"
            / "pi"
            / "node_modules"
            / ".bin"
            / "pi.cmd"
        )
        cli.parent.mkdir(parents=True)
        cli.write_text("@echo off\n", encoding="utf-8")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
            self.assertEqual(candidate_executable("pi", "pi"), str(cli))

    def test_unknown_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            apply_proposal(
                self.root, '{"files":{"src/tempo.py":"x"},"shell":"del C:/"}'
            )

    def test_untracked_outside_file_fails_acceptance(self):
        apply_proposal(self.root, json.dumps({"files": {"src/tempo.py": GOOD}}))
        (self.root / "extra.py").write_text("x=1\n", encoding="utf-8")
        self.assertEqual(verify(self.root)["unexpected_files"], ["extra.py"])
        self.assertFalse(verify(self.root)["accepted"])


if __name__ == "__main__":
    unittest.main()
