from pathlib import Path
from scripts import autonomous_slice_builder as b

def test_protected_governance_paths_blocked():
    assert b.protected_change(["DoWork.txt"])=="DoWork.txt"
    assert b.protected_change([".github/workflows/ci.yml"])==".github/workflows/ci.yml"
    assert b.protected_change(["docs/ARCHITECTURE.md"])=="docs/ARCHITECTURE.md"

def test_source_and_tests_are_allowed():
    assert b.protected_change(["src/app.py","tests/test_app.py"]) is None

def test_prompt_forbids_human_and_product_reality_boundaries():
    text=b.prompt_for("jweter/Project-Everward")
    assert "ONE small" in text
    assert "does not require a human decision" in text
    assert "Do not claim Product Reality" in text

def test_select_repo_rotates_and_skips_open_prs(monkeypatch,tmp_path:Path):
    monkeypatch.setattr(b,"has_open_pr",lambda repo: repo.endswith("project-orchestrator"))
    chosen=b.select_repo(tmp_path/"state.json")
    assert chosen=="jweter/knowledge-engine-core"

def test_select_repo_returns_none_when_every_repo_owned(monkeypatch,tmp_path:Path):
    monkeypatch.setattr(b,"has_open_pr",lambda repo: True)
    assert b.select_repo(tmp_path/"state.json") is None


def test_run_aider_uses_argument_vector_and_local_ollama(monkeypatch,tmp_path):
    seen={}
    class R:
        returncode=0
    def fake_run(argv,cwd,**kwargs):
        seen["argv"]=argv; seen["cwd"]=cwd; seen["env"]=kwargs["env"]; return R()
    monkeypatch.setattr(b,"run",fake_run)
    b.run_aider(tmp_path,"one bounded slice")
    assert seen["argv"][0]=="aider"
    assert seen["argv"][seen["argv"].index("--message")+1]=="one bounded slice"
    assert seen["env"]["OLLAMA_API_BASE"]=="http://127.0.0.1:11434"


def test_installer_uses_current_user_startup_supervisor():
    installer=(Path(__file__).resolve().parents[1]/"scripts"/"install_continuous_flow.ps1").read_text(encoding="utf-8")
    assert '[Environment]::GetFolderPath("Startup")' in installer
    assert "ExponentialEngine-ContinuousFlow.cmd" in installer
    assert "continuous_flow_supervisor.py" in installer
    assert "Green PR merger: 60-second poll" in installer
    assert "Autonomous builder: 5-minute loop" in installer
