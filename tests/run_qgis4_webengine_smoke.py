import sys
from pathlib import Path

# QGIS runs --code scripts through exec(), which leaves __file__ undefined and sys.argv empty.
# `make qgis-smoke` runs from the project root, which is what QGIS resolved the script path against.
project_root = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(project_root.parent))
sys.path.insert(0, str(project_root / "tests"))
# appended, not prepended: the root holds CCD_Plugin.py, which would shadow the CCD_Plugin package
sys.path.append(str(project_root))

# --noplugins skips classFactory, which is what puts the bundled plotly on the path and registers
# the icons under :/plugins/CCD_Plugin/
from CCD_Plugin import pre_init_plugin, resources  # noqa: E402, F401

pre_init_plugin()

from test_qgis4_webengine import run_from_qgis  # noqa: E402

run_from_qgis()
