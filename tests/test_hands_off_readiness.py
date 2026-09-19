import json
import subprocess
from pathlib import Path

from scripts.hands_off_readiness import audit


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "commit", "-qm", "fixture"],
        check=True,
    )
    return repo


def test_readiness_passes_with_path_and_text_evidence(tmp_path: Path) -> None:
    contract = {"required_authority": [
        {"id": "roadmap", "evidence_any": ["ROADMAP.md"]},
        {"id": "privacy", "text_signal_any": ["privacy"]},
    ]}
    repo = _repo(tmp_path, {"ROADMAP.md": "Roadmap\n\nPrivacy boundary."})
    report = audit(repo, contract)
    assert report["status"] == "PASS"
    assert report["head"]


def test_missing_required_evidence_fails_closed(tmp_path: Path) -> None:
    contract = {"required_authority": [
        {"id": "architecture", "evidence_any": ["ARCHITECTURE.md"]},
    ]}
    repo = _repo(tmp_path, {"README.md": "hello"})
    report = audit(repo, contract)
    assert report["status"] == "FAIL"
    assert report["checks"][0]["status"] == "FAIL"


def test_malformed_requirement_is_unknown(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"README.md": "hello"})
    report = audit(repo, {"required_authority": [{"id": "bad"}]})
    assert report["status"] == "UNKNOWN"
