import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import qgis.PyQt.QtWidgets  # noqa: F401
except ImportError:
    QGIS_AVAILABLE = False
else:
    QGIS_AVAILABLE = True

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@unittest.skipUnless(QGIS_AVAILABLE, "QGIS Python bindings are required")
class PlotlyBootstrapTest(unittest.TestCase):
    """How classFactory makes plotly importable, with no plotly anywhere but where a test puts it."""

    def setUp(self):
        import CCD_Plugin as package

        self.package = package
        self.bundle = tempfile.mkdtemp(prefix="ccd-extlibs-")
        self.addCleanup(shutil.rmtree, self.bundle, True)
        no_plotly_path = [path for path in sys.path if not os.path.exists(os.path.join(path, "plotly"))]
        plotly_modules = [name for name in sys.modules if name == "plotly" or name.startswith(("plotly.", "_plotly"))]
        for patcher in (
            patch.object(package, "EXTRA_LIBS_PATH", self.bundle),
            patch.object(sys, "path", no_plotly_path),
            patch.dict(sys.modules),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in plotly_modules:
            del sys.modules[name]
        importlib.invalidate_caches()
        self.install = Mock()
        install_patcher = patch.object(package.extralibs, "install", self.install)
        install_patcher.start()
        self.addCleanup(install_patcher.stop)

    def installed(self, version):
        patcher = patch.object(self.package, "_installed_plotly_version", return_value=version)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bundle_plotly(self, source='__version__ = "6.7.0"\n'):
        os.makedirs(os.path.join(self.bundle, "plotly"))
        Path(self.bundle, "plotly", "__init__.py").write_text(source, encoding="utf-8")

    def test_the_bundle_replaces_an_installed_plotly_too_old(self):
        # Given: plotly 5.4 installed, too old, and the bundle downloaded.
        self.installed("5.4.0")
        self.bundle_plotly()

        # When/Then: the bundle goes first, or the old one would always be the one imported.
        self.assertIsNone(self.package.ensure_plotly())
        self.assertEqual(sys.path[0], self.bundle)
        self.install.assert_not_called()

    def test_the_bundle_goes_behind_everything_installed_otherwise(self):
        # Given: no plotly installed, so nothing to replace.
        self.installed(None)
        self.bundle_plotly()

        # When/Then: appended, so the bundled packaging and narwhals do not shadow the installed
        # ones for every other plugin.
        self.assertIsNone(self.package.ensure_plotly())
        self.assertEqual(sys.path[-1], self.bundle)

    def test_a_present_bundle_is_not_downloaded_again_when_an_old_plotly_is_loaded(self):
        # Given: the bundle downloaded, but an old plotly already imported before the plugin.
        self.installed("5.4.0")
        self.bundle_plotly()
        sys.modules["plotly"] = types.SimpleNamespace(__version__="5.4.0")

        # When: the plugin loads, as it does on every QGIS start.
        problem = self.package.ensure_plotly()

        # Then: nothing is downloaded again, and the user learns which plotly is in the way.
        self.install.assert_not_called()
        self.assertIn("plotly 5.4.0 was loaded before it", problem)

    def test_a_broken_bundle_is_fetched_again(self):
        # Given: a bundle that fails to import, as an interrupted copy leaves it.
        self.installed(None)
        self.bundle_plotly("raise ImportError('half extracted')\n")

        def reinstall():
            Path(self.bundle, "plotly", "__init__.py").write_text('__version__ = "6.7.0"\n', encoding="utf-8")

        self.install.side_effect = reinstall

        # When/Then: it is downloaded again once, and plotly imports.
        self.assertIsNone(self.package.ensure_plotly())
        self.install.assert_called_once_with()

    def site_with_plotly(self, version):
        """A directory on sys.path holding a plotly package, without package metadata."""
        site = tempfile.mkdtemp(prefix="ccd-site-")
        self.addCleanup(shutil.rmtree, site, True)
        os.makedirs(os.path.join(site, "plotly"))
        Path(site, "plotly", "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        sys.path.append(site)
        return site

    def test_an_installed_plotly_is_found_without_being_imported(self):
        # Given/When/Then: none installed reads as None, one without metadata as version "0".
        self.assertIsNone(self.package._installed_plotly_version())
        self.site_with_plotly("5.4.0")
        self.assertEqual(self.package._installed_plotly_version(), "0")
        self.assertNotIn("plotly", sys.modules)

    def test_the_bundle_replaces_a_plotly_of_unknown_version(self):
        # Given: an old plotly installed without the metadata its version is read from, and the
        # bundle downloaded.
        self.site_with_plotly("5.4.0")
        self.bundle_plotly()

        # When/Then: the bundle, known to work, goes first and is the plotly imported.
        self.assertIsNone(self.package.ensure_plotly())
        self.assertEqual(sys.path[0], self.bundle)
        self.install.assert_not_called()

    def test_an_old_installed_plotly_with_a_failed_download_is_reported_as_such(self):
        # Given: plotly 5.4 installed, imported by nothing yet, and no network for the bundle.
        self.site_with_plotly("5.4.0")
        self.installed("5.4.0")

        # When: the plugin loads.
        problem = self.package.ensure_plotly()

        # Then: one download was tried, and the message names the failed download - not some other
        # plugin, which never loaded plotly.
        self.install.assert_called_once_with()
        self.assertIn("installed plotly 5.4.0 is too old", problem)
        self.assertIn("could not be downloaded", problem)
        self.assertNotIn("another plugin", problem)

    def test_a_failed_download_is_not_retried_on_the_same_start(self):
        # Given: no plotly at all, and no network.
        self.installed(None)

        # When: the plugin loads.
        problem = self.package.ensure_plotly()

        # Then: one download attempt, and the install instructions.
        self.install.assert_called_once_with()
        self.assertIn("install instructions", problem)


if __name__ == "__main__":
    unittest.main()
