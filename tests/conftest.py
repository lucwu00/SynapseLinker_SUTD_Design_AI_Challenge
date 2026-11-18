"""Most navigation tests use the multi-corridor example map; the table loop has its own tests."""
import os
from pathlib import Path

os.environ.setdefault("SYNAPSE_MAP", str(Path(__file__).resolve().parents[1] / "config" / "classroom_map.example.json"))
