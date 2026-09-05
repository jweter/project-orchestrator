from pathlib import Path

from project_orchestrator.learning import load_memory

ROOT = Path(__file__).resolve().parents[1]
memory = load_memory(ROOT / "engineering" / "learning-memory.json")
print(f"LEARNING MEMORY: PASS ({len(memory['entries'])} verified lessons)")
