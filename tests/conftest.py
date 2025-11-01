import sys
from pathlib import Path

pytest_plugins = ("pytest_homeassistant_custom_component",)


# Ensure the src directory is on sys.path for imports during testing.
src_path = Path(__file__).resolve().parents[1] / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
