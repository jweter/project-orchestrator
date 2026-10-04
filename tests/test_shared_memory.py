import json
from pathlib import Path

from project_orchestrator.learning import validate_lesson
from project_orchestrator.shared_memory import (
    build_hierarchical_index,
    load_shared_memory,
    query_all_memory,
    query_memory,
    render_text,
    sync_shared_memory,
)


def _memory():
    lesson = validate_lesson(
        {
            "project": "portfolio",
            "repository": "jweter/project-orchestrator",
            "source_type": "ci_failure",
            "source_ref": "project-orchestrator#99",
            "symptom": "Windows worker failed on POSIX-only process signaling.",
            "root_cause": "A POSIX-only kill primitive crossed a Windows compatibility boundary.",
            "fix": "Use a platform-aware process termination helper.",
            "verification": "Exact-head preflight passed on Windows.",
            "regression_protection": "Regression test covers the Windows branch.",
            "residual_risk": "Other platform-specific process APIs require the same boundary review.",
            "evidence_state": "VERIFIED",
        }
    )
    return {"schema_version": 1, "entries": [lesson]}


def test_hierarchical_index_groups_verified_lessons() -> None:
    index = build_hierarchical_index(_memory())
    ids = index["tree"]["portfolio"]["jweter/project-orchestrator"]["ci_failure"]
    assert ids == [_memory()["entries"][0]["lesson_id"]]


def test_query_requires_term_overlap_when_terms_are_supplied() -> None:
    result = query_memory(
        _memory(),
        repository="jweter/project-orchestrator",
        terms=["Windows", "process"],
    )
    assert result["state"] == "MATCHES"
    assert result["results"][0]["lesson"]["source_ref"] == "project-orchestrator#99"

    miss = query_memory(
        _memory(),
        repository="jweter/project-orchestrator",
        terms=["musicxml"],
    )
    assert miss["state"] == "NO_MATCH"


def test_sync_materializes_json_txt_and_index(tmp_path: Path) -> None:
    result = sync_shared_memory(_memory(), tmp_path)
    assert result["state"] == "SYNCED"
    assert (tmp_path / "memory.json").exists()
    assert (tmp_path / "memory.txt").exists()
    assert (tmp_path / "index.json").exists()
    assert load_shared_memory(tmp_path)["entries"][0]["evidence_state"] == "VERIFIED"

    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["entry_count"] == 1


def test_text_export_marks_memory_as_non_authoritative() -> None:
    rendered = render_text(_memory())
    assert "current live evidence always outrank memory" in rendered
    assert "evidence_state: VERIFIED" in rendered


def test_query_all_memory_reads_repository_snapshots(tmp_path: Path) -> None:
    sync_shared_memory(_memory(), tmp_path)
    repo_dir = tmp_path / "repos"
    repo_dir.mkdir()
    snapshot = {
        "schema_version": 1,
        "kind": "azathoth-repository-learning-snapshot",
        "repository": "jweter/VoiceStudio",
        "events": [
            {
                "type": "ci_failure",
                "repository": "jweter/VoiceStudio",
                "summary": "Windows renderer startup failed after a stale port reservation.",
                "root_cause": "A stale local process retained the development port.",
                "successful_pattern": "Detect and clear only the owned stale process before launch.",
                "verification": "Exact-head repository preflight passed.",
                "residual_risk": "Unowned processes must remain untouched.",
                "source_ref": "VoiceStudio#test",
                "evidence_state": "VERIFIED",
            }
        ],
    }
    (repo_dir / "jweter__VoiceStudio.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )

    result = query_all_memory(
        root=tmp_path,
        repository="jweter/VoiceStudio",
        terms=["Windows", "renderer"],
    )

    assert result["state"] == "MATCHES"
    assert result["results"][0]["source"] == "repository"
    assert result["results"][0]["record"]["repository"] == "jweter/VoiceStudio"
