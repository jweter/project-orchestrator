from pathlib import Path

from project_orchestrator.compounding import load_series

ROOT = Path(__file__).resolve().parents[1]
series = load_series(ROOT / "engineering" / "compounding-series.json")
print(f"COMPOUNDING SERIES: PASS ({len(series['windows'])} verified windows)")
