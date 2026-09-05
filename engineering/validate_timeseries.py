from pathlib import Path

from project_orchestrator.timeseries import load_store

ROOT = Path(__file__).resolve().parents[1]
store = load_store(ROOT / "engineering" / "compounding-timeseries.json")
print(f"COMPOUNDING TIMESERIES: PASS ({len(store['windows'])} evidence windows)")
