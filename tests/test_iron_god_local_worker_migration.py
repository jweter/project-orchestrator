"""Provenance of the legacy IronGodLocal worker brought into version control."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "engineering" / "iron-god-local-worker-migration.json").read_text(encoding="utf-8"))
WORKER = ROOT / "scripts" / "iron_god_local" / "local_continuous_worker.py"


def _blob(blob_id: str) -> bytes:
    result = subprocess.run(["git", "-C", str(ROOT), "cat-file", "blob", blob_id],
                            capture_output=True, check=False)
    if result.returncode != 0:
        pytest.skip("import commit history unavailable in this checkout")
    return result.stdout


def test_import_commit_holds_byte_identical_installed_sources():
    """The unmodified import is the exact installed origin of every later change."""
    for name, record in MANIFEST["modules"].items():
        assert hashlib.sha256(_blob(record["imported_git_blob"])).hexdigest() == record["installed_sha256"], name


def test_manifest_records_rollback_and_obsolete_authority():
    assert MANIFEST["observed_runtime_state"]["scheduled_task_launching_worker"] == "none"
    assert any("human-mode.json" in item for item in MANIFEST["obsolete"])
    assert any("installed_sha256" in step for step in MANIFEST["rollback"])


def test_migrated_worker_no_longer_reads_retired_authority():
    source = WORKER.read_text(encoding="utf-8")
    gate = source[source.index("def gate() -> bool:"):source.index("def controller_payload()")]
    assert "evaluate_execution_authority" in gate and "IRON_GOD_ACCEPTED_SHA" in gate
    assert "human-mode.json" not in source
    assert "iron-god-exclusive" not in source
    assert "authorized_fn = gate\n" in source
    # Code and live state are distinct: the repository is never used as state.
    assert "CODE_DIR = Path(__file__).resolve().parent" in source
    assert "BASE = Path(__file__).resolve().parent" not in source


def test_worker_state_root_defaults_to_installed_location(monkeypatch, tmp_path):
    import importlib.util

    monkeypatch.delenv("IRON_GOD_LOCAL_BASE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    spec = importlib.util.spec_from_file_location("migrated_worker_default_base", WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.BASE == (tmp_path / "ExponentialEngine" / "IronGodLocal").resolve()
    assert module.FLOW == module.BASE.parent / "flow-state"
    assert module.CODE_DIR == WORKER.parent
    monkeypatch.setenv("IRON_GOD_LOCAL_BASE", str(tmp_path / "custom"))
    spec = importlib.util.spec_from_file_location("migrated_worker_custom_base", WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.BASE == (tmp_path / "custom").resolve()


def test_migrated_gate_denies_without_owner_grant_and_follows_one(monkeypatch, tmp_path):
    import importlib.util

    from tests.owner_authority import TEST_RELEASE_SHA, grant_owner_authority, revoke_owner_authority

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("IRON_GOD_LOCAL_BASE", raising=False)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", TEST_RELEASE_SHA)
    spec = importlib.util.spec_from_file_location("migrated_worker_gate", WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flow = module.FLOW
    flow.mkdir(parents=True)
    (flow / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    (flow / "iron-god-exclusive.json").write_text('{"enabled": true, "target_product": "iron-god"}', encoding="utf-8")
    assert module.gate() is False  # released retired markers grant nothing
    grant_owner_authority(flow)
    assert module.gate() is True
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", "b" * 40)
    assert module.gate() is False  # grant is bound to its release
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", TEST_RELEASE_SHA)
    revoke_owner_authority(flow)
    assert module.gate() is False
