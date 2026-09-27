from __future__ import annotations

from pathlib import Path

from project_orchestrator.inventory import load_inventory

ROOT = Path(__file__).resolve().parents[1]
inventory = load_inventory(ROOT / "engineering" / "portfolio.json")
assert len(inventory["projects"]) == 4
assert sum(len(project["repositories"]) for project in inventory["projects"]) == 6
print("PORTFOLIO INVENTORY: PASS")

\n