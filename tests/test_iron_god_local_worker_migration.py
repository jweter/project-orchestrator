"""Provenance of the legacy IronGodLocal worker brought into version control."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "engineering" / "iron-god-local-worker-migration.json").read_text(encoding="utf-8"))


def test_imported_modules_are_byte_identical_to_the_recorded_installed_sources():
    for name, record in MANIFEST["modules"].items():
        data = (ROOT / record["repository_path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["installed_sha256"], name


def test_manifest_records_rollback_and_obsolete_authority():
    assert MANIFEST["observed_runtime_state"]["scheduled_task_launching_worker"] == "none"
    assert any("human-mode.json" in item for item in MANIFEST["obsolete"])
    assert any("installed_sha256" in step for step in MANIFEST["rollback"])
