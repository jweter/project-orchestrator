from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def load_guard():
    path = Path(__file__).with_name("conftest.py")
    spec = importlib.util.spec_from_file_location("provenance_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provenance_guard_accepts_exact_checkout():
    guard = load_guard()
    assert guard._package_root() == guard.EXPECTED_PACKAGE_ROOT


def test_provenance_guard_rejects_foreign_checkout(monkeypatch, tmp_path):
    guard = load_guard()
    foreign = tmp_path / "project_orchestrator"
    foreign.mkdir()
    fake_init = foreign / "__init__.py"
    fake_init.write_text("", encoding="utf-8")
    monkeypatch.setattr(guard.project_orchestrator, "__file__", str(fake_init))
    with pytest.raises(RuntimeError, match="IMPORT_PROVENANCE_MISMATCH"):
        guard.pytest_sessionstart(None)
