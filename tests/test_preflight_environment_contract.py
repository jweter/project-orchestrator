from pathlib import Path


def test_preflight_isolates_imports_and_temp_root():
    text = (Path(__file__).resolve().parents[1] / "engineering" / "preflight.py").read_text(
        encoding="utf-8"
    )
    assert 'PREFLIGHT_ENV["PYTHONPATH"] = str(SRC)' in text
    assert 'PREFLIGHT_ENV.get("PYTHONPATH"' not in text
    assert 'PREFLIGHT_BASETEMP.parent.mkdir(parents=True, exist_ok=True)' in text
    assert '"--basetemp", str(PREFLIGHT_BASETEMP)' in text
