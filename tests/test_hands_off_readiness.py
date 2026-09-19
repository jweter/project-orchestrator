import json
import subprocess
from pathlib import Path

from scripts.hands_off_readiness import activation_allowed, audit


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


def test_activation_requires_complete_exact_head_pass() -> None:
    assert activation_allowed({
        "status": "PASS",
        "repositories": [{"status": "PASS", "head": "abc123"}],
    })
    assert not activation_allowed({
        "status": "PASS",
        "repositories": [{"status": "PASS", "head": None}],
    })
    assert not activation_allowed({
        "status": "FAIL",
        "repositories": [{"status": "PASS", "head": "abc123"}],
    })
    assert not activation_allowed({"status": "PASS", "repositories": []})

def test_main_writes_activation_state_for_green_report(tmp_path, monkeypatch):
    import json, sys
    import scripts.hands_off_readiness as readiness
    contract=tmp_path/"contract.json"
    contract.write_text(json.dumps({"required_authority":[{"id":"roadmap","evidence_any":["ROADMAP.md"]}]}),encoding="utf-8")
    repo=tmp_path/"repo"; repo.mkdir()
    import subprocess
    subprocess.run(["git","init",str(repo)],check=True,capture_output=True)
    subprocess.run(["git","-C",str(repo),"config","user.email","test@example.com"],check=True)
    subprocess.run(["git","-C",str(repo),"config","user.name","Test"],check=True)
    (repo/"ROADMAP.md").write_text("roadmap",encoding="utf-8")
    subprocess.run(["git","-C",str(repo),"add","ROADMAP.md"],check=True)
    subprocess.run(["git","-C",str(repo),"commit","-m","init"],check=True,capture_output=True)
    activation=tmp_path/"state"/"activation.json"
    monkeypatch.setattr(sys,"argv",["hands_off_readiness.py",str(repo),"--contract",str(contract),"--activation-state",str(activation)])
    assert readiness.main()==0
    payload=json.loads(activation.read_text(encoding="utf-8"))
    assert payload["enabled"] is True
    assert payload["reason"]=="READINESS_PASS"
    assert payload["heads"][str(repo.resolve())]
