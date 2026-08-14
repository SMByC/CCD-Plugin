import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root.parent))
sys.path.insert(0, str(project_root / "tests"))

from test_qgis4_webengine import run_from_qgis  # noqa: E402

run_from_qgis()
