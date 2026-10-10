import runpy
from pathlib import Path


def test_preflight_isolates_imports_and_temp_root():
    text = (Path(__file__).resolve().parents[1] / "engineering" / "preflight.py").read_text(
        encoding="utf-8"
    )
    assert 'PREFLIGHT_ENV["PYTHONPATH"] = str(SRC)' in text
    assert 'PREFLIGHT_ENV.get("PYTHONPATH"' not in text
    assert 'PREFLIGHT_BASETEMP = ROOT.parent / ".preflight-tmp" / f"{ROOT.name}-{os.getpid()}-{time.time_ns()}"' in text
    assert 'PREFLIGHT_BASETEMP.parent.mkdir(parents=True, exist_ok=True)' in text
    assert '"--basetemp", str(PREFLIGHT_BASETEMP)' in text
    assert "import shutil" in text
    assert "shutil.rmtree(PREFLIGHT_BASETEMP, ignore_errors=True)" in text
    assert "os.getpid()" in text
    assert "time.time_ns()" in text


def test_preflight_basetemp_is_unique_per_run():
    path = Path(__file__).resolve().parents[1] / "engineering" / "preflight.py"
    first = runpy.run_path(str(path), run_name="preflight_contract_one")["PREFLIGHT_BASETEMP"]
    second = runpy.run_path(str(path), run_name="preflight_contract_two")["PREFLIGHT_BASETEMP"]
    assert first != second
    assert first.parent == second.parent
