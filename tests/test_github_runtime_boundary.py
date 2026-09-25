from pathlib import Path
import scripts.autonomous_slice_builder as builder

class Result:
    returncode=0; stdout='{"ok":true}'; stderr=""

def test_gh_json_uses_foundry_run_boundary(monkeypatch,tmp_path):
    seen=[]
    def fake(argv,cwd,**kwargs):
        seen.append((argv,cwd)); return Result()
    monkeypatch.setattr(builder,"run",fake)
    assert builder.gh_json(["api","x"],tmp_path)=={"ok":True}
    assert seen==[(["gh","api","x"],tmp_path)]

def test_source_has_no_direct_gh_subprocess_bypass():
    source=Path(builder.__file__).read_text(encoding="utf-8")
    assert 'subprocess.run(["gh"' not in source
