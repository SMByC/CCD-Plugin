import os
import sys
import tempfile
import unittest
from pathlib import Path

RUN_QGIS4_SMOKE = os.environ.get("CCD_RUN_QGIS4_SMOKE") == "1"


@unittest.skipUnless(RUN_QGIS4_SMOKE, "set CCD_RUN_QGIS4_SMOKE=1 inside QGIS 4 to run WebEngine smoke tests")
class Qgis4WebEngineSmokeTest(unittest.TestCase):
    def test_plugin_dock_instantiates_with_webengine_only(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from qgis.PyQt.QtWebEngineCore import QWebEngineSettings
        from qgis.PyQt.QtWebEngineWidgets import QWebEngineView
        from qgis.utils import iface

        plugin = CCD_Plugin(iface)
        plugin.initGui()
        try:
            plugin.run()
            widget = plugin.widget
            if widget is None:
                self.fail("Plugin run did not create its dock widget")
            self.assertIsInstance(widget.plot_webview, QWebEngineView)
            self.assertFalse(
                widget.plot_webview.settings().testAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls
                )
            )
        finally:
            plugin.unload()

    def test_self_contained_plot_loads_and_renders(self):
        from qgis.core import Qgis
        from qgis.PyQt.QtCore import QEventLoop, QTimer, QUrl
        from qgis.PyQt.QtWebEngineCore import QWebEngineSettings
        from qgis.PyQt.QtWebEngineWidgets import QWebEngineView

        from core.plot import PlotSpec, PlotStyle, build_figure, write_plot_html

        self.assertTrue(Qgis.version().startswith("4."))
        with tempfile.TemporaryDirectory() as temporary_directory:
            html_path = Path(temporary_directory) / "smoke.html"
            figure = build_figure(
                {},
                {"time": [0.0], "B4": [1.0]},
                PlotSpec(dataset="Smoke", band="B4", longitude=0.0, latitude=0.0),
            )
            write_plot_html(figure, html_path, image_filename="ccd_smoke", style=PlotStyle.LIGHT)

            view = QWebEngineView()
            settings = view.settings()
            if settings is None:
                self.fail("QWebEngineView returned no settings object")
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)

            loop = QEventLoop()
            load_result = []
            view.loadFinished.connect(lambda succeeded: (load_result.append(succeeded), loop.quit()))
            view.load(QUrl.fromLocalFile(str(html_path)))
            QTimer.singleShot(30_000, loop.quit)
            loop.exec()
            self.assertEqual(load_result, [True])

            rendered = []
            page = view.page()
            if page is None:
                self.fail("QWebEngineView returned no page object")
            page.runJavaScript(
                "typeof Plotly === 'object' && document.querySelectorAll('.js-plotly-plot').length === 1",
                lambda result: (rendered.append(result), loop.quit()),
            )
            QTimer.singleShot(10_000, loop.quit)
            loop.exec()
            self.assertEqual(rendered, [True])
            view.close()


if __name__ == "__main__":
    unittest.main()


def run_from_qgis() -> None:
    from qgis.PyQt.QtCore import QCoreApplication, QTimer

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Qgis4WebEngineSmokeTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.stdout.flush()
    exit_code = 0 if result.wasSuccessful() else 1
    QTimer.singleShot(0, lambda: QCoreApplication.exit(exit_code))
