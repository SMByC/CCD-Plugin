import configparser
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Qgis4MigrationContractTest(unittest.TestCase):
    def test_metadata_targets_qgis4_only(self):
        # Given: the plugin repository metadata.
        metadata = configparser.ConfigParser()
        metadata.read(PROJECT_ROOT / "metadata.txt", encoding="utf-8")

        # When: compatibility fields are inspected.
        general = metadata["general"]

        # Then: QGIS 4 is the sole supported runtime.
        self.assertEqual(general["qgisMinimumVersion"], "4.0")
        self.assertNotIn("qgisMaximumVersion", general)
        self.assertNotIn("supportsQt6", general)

    def test_runtime_has_no_webkit_or_qt5_fallback(self):
        # Given: only plugin-owned runtime sources and Designer forms.
        source_paths = [PROJECT_ROOT / "__init__.py", PROJECT_ROOT / "CCD_Plugin.py"]
        for source_root in ("core", "gui", "utils", "ui"):
            source_paths.extend((PROJECT_ROOT / source_root).rglob("*.py"))
            source_paths.extend((PROJECT_ROOT / source_root).rglob("*.ui"))
        shipped_sources = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)

        # When/Then: only QGIS' Qt6 WebEngine bridge remains.
        for legacy_token in ("QtWebKit", "QWebView", "QWebSettings", "HAS_WEBKIT", "HAS_WEBENGINE", "PyQt5"):
            self.assertNotIn(legacy_token, shipped_sources)
        self.assertFalse((PROJECT_ROOT / "ui" / "CCD_Plugin_dockwidget_QWebView.ui").exists())

    def test_retained_ui_files_use_scoped_qt6_enums(self):
        # Given: both retained Designer files.
        documents = [
            (PROJECT_ROOT / "ui" / name).read_text(encoding="utf-8")
            for name in ("CCD_Plugin_dockwidget_QWebEngine.ui", "advanced_settings.ui")
        ]

        # When/Then: common Qt5 unscoped forms are absent.
        for document in documents:
            for unscoped_enum in (
                r"Qt::Horizontal(?=<)",
                r"Qt::Align\w+(?=[|<])",
                r"QAbstractSpinBox::NoButtons(?=<)",
                r"QComboBox::AdjustToContents(?=<)",
                r"QDialogButtonBox::Ok(?=<)",
            ):
                self.assertIsNone(re.search(unscoped_enum, document))


if __name__ == "__main__":
    unittest.main()
