import sys
from pathlib import Path

# Make the model package importable in tests without installation.
_MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "climate_index_plotter"
sys.path.insert(0, str(_MODEL_DIR))
