"""Run the QGIS smoke tests in a bare QgsApplication, without a display or a QGIS window.

`make qgis-smoke` runs them inside QGIS through --code; that needs QGIS to start, which it does
not do headless. Here the application is built directly:

    QT_QPA_PLATFORM=offscreen python tests/run_qgis_smoke_headless.py   (make qgis-smoke-headless)
"""

import os
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root.parent))
sys.path.insert(0, str(project_root / "tests"))
# appended, not prepended: the root holds CCD_Plugin.py, which would shadow the CCD_Plugin package
sys.path.append(str(project_root))
os.environ["CCD_RUN_QGIS4_SMOKE"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

# Qt WebEngine has to be imported before the application exists
from qgis.PyQt.QtWebEngineWidgets import QWebEngineView  # noqa: E402, F401, I001

from qgis.core import QgsApplication  # noqa: E402
from qgis.PyQt.QtWidgets import QStyleFactory  # noqa: E402

# argv must not be empty (bytes for these bindings), and the platform style crashes offscreen
application = QgsApplication([b"ccd-plugin-smoke"], True)
application.setStyle(QStyleFactory.create("Fusion"))
application.initQgis()

import qgis_host  # noqa: E402

qgis_host.install()

from CCD_Plugin import pre_init_plugin, resources  # noqa: E402, F401

pre_init_plugin()

import test_plot_supersession  # noqa: E402
import test_qgis4_webengine  # noqa: E402
import test_qgis_lifecycle  # noqa: E402

loader = unittest.defaultTestLoader
suite = unittest.TestSuite(
    [
        loader.loadTestsFromModule(test_qgis4_webengine),
        loader.loadTestsFromModule(test_plot_supersession),
        loader.loadTestsFromModule(test_qgis_lifecycle),
    ]
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
application.exitQgis()
sys.exit(0 if result.wasSuccessful() else 1)
