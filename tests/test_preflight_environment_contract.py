from pathlib import Path


def test_preflight_isolates_imports_and_temp_root():
    text = (Path(__file__).resolve().parents[1] / "engineering" / "preflight.py").read_text(
        encoding="utf-8"
    )
    assert 'PREFLIGHT_ENV["PYTHONPATH"] = str(SRC)' in text
    assert 'PREFLIGHT_ENV.get("PYTHONPATH"' not in text
    # The former ROOT.parent-based path broke nested Git fixture creation on
    # Windows (Filename too long). Require a short, separately keyed temp root.
    assert 'PREFLIGHT_BASETEMP = ROOT.parent / ".preflight-tmp" / ROOT.name' not in text
    assert 'os.path.normcase(str(ROOT.resolve()))' in text
    assert "hashlib.sha256(" in text
    assert (
        'PREFLIGHT_BASETEMP = Path(tempfile.gettempdir()) / "ig-preflight" / _ROOT_KEY'
        in text
    )
    assert 'PREFLIGHT_BASETEMP.parent.mkdir(parents=True, exist_ok=True)' in text
    assert '"--basetemp", str(PREFLIGHT_BASETEMP)' in text
    assert "import shutil" in text
    assert "shutil.rmtree(PREFLIGHT_BASETEMP, ignore_errors=True)" in text
    assert "os.getpid()" not in text