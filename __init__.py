"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019-2026 by Xavier Corredor Llano, SMByC
        email                : xavier.corredor.llano@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
 This script initializes the plugin, making it known to QGIS.
"""

import importlib
import importlib.machinery
import importlib.metadata
import os
import site
import sys

from qgis.PyQt.QtWidgets import QMessageBox

from CCD_Plugin.utils import extralibs
from CCD_Plugin.utils.versions import MIN_PLOTLY_VERSION, version_satisfies

EXTRA_LIBS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "extlibs"))


def _installed_plotly_version() -> str | None:
    """Version of a plotly installed outside the bundle, found without importing it.

    None when there is none. "0", too old to use, when one is found without the package metadata
    its version is read from: the bundle is known to work, a plotly of unknown version is not.
    """
    outside_bundle = [path for path in sys.path if os.path.abspath(path or os.curdir) != EXTRA_LIBS_PATH]
    if importlib.machinery.PathFinder.find_spec("plotly", outside_bundle) is None:
        return None
    try:
        return importlib.metadata.version("plotly")
    except importlib.metadata.PackageNotFoundError:
        return "0"


def _imported_plotly_version() -> str | None:
    """Version of the plotly `import plotly` gives, or None when there is none."""
    try:
        import plotly
    except ImportError:
        return None
    return getattr(plotly, "__version__", "0")


def _bundle_present() -> bool:
    return os.path.isdir(os.path.join(EXTRA_LIBS_PATH, "plotly"))


def _usable_plotly_available() -> bool:
    """Whether a recent enough plotly can be imported, decided without importing one.

    Importing first would pin a too old installed plotly for the whole session, before the
    bundle that replaces it is downloaded. A bundle already there is never downloaded again.
    """
    if _bundle_present():
        return True
    if "plotly" in sys.modules:
        return version_satisfies(getattr(sys.modules["plotly"], "__version__", "0"), MIN_PLOTLY_VERSION)
    installed = _installed_plotly_version()
    return installed is not None and version_satisfies(installed, MIN_PLOTLY_VERSION)


def check_dependencies() -> bool:
    """Return True if all required extra libraries are importable, in a version the plugin works with."""
    version = _imported_plotly_version()
    return version is not None and version_satisfies(version, MIN_PLOTLY_VERSION)


def pre_init_plugin() -> None:
    """Add the bundled *extlibs* directory into plugin folder so that extra
    Python packages can be imported before loading the plugin.

    Appended, behind everything installed, except to replace an installed plotly that is too
    old: appended then, the bundle could never be imported, since the installed one is found
    first. Prepending in any other case would also put the bundled packaging and narwhals ahead of
    the installed ones for every plugin. Once plotly is imported the order no longer matters.
    """
    if not os.path.isdir(EXTRA_LIBS_PATH) or EXTRA_LIBS_PATH in sys.path:
        return
    installed = _installed_plotly_version()
    if "plotly" not in sys.modules and installed is not None and not version_satisfies(installed, MIN_PLOTLY_VERSION):
        sys.path.insert(0, EXTRA_LIBS_PATH)
    else:
        site.addsitedir(EXTRA_LIBS_PATH)


def ensure_plotly() -> str | None:
    """Make a recent enough plotly importable, installing the bundle if needed.

    Returns what to tell the user when that is not possible, None when it is.
    """
    # a plotly imported before this plugin loaded is the one used for the rest of the session
    loaded_before = "plotly" in sys.modules
    downloaded = False
    if not _usable_plotly_available():
        # Extra libs missing, or the installed plotly is too old - download and install them
        extralibs.install()
        downloaded = True
    pre_init_plugin()
    if not downloaded and _bundle_present() and not check_dependencies() and "plotly" not in sys.modules:
        # the bundle is there but nothing imports from it: it is broken, so fetch it again
        extralibs.install()
        importlib.invalidate_caches()
        pre_init_plugin()
    if check_dependencies():
        return None

    found = _imported_plotly_version()
    minimum = ".".join(map(str, MIN_PLOTLY_VERSION))
    if found is None:
        reason = ""
    elif loaded_before:
        reason = (
            f"It needs plotly {minimum} or newer, but plotly {found} was loaded before it, by QGIS or another "
            "plugin. Update plotly in the QGIS Python environment.\n\n"
        )
    else:
        # the installed plotly is too old, and the bundle that replaces it could not be installed
        reason = (
            f"It needs plotly {minimum} or newer: the installed plotly {found} is too old, and the bundled "
            "libraries could not be downloaded. Restart QGIS to try again, or update plotly in the QGIS "
            "Python environment.\n\n"
        )
    return (
        "Error loading libraries for CCD-Plugin.\n\n" + reason + "Read the install instructions here:\n"
        "https://github.com/SMByC/CCD-Plugin#installation"
    )


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load CCD_Plugin class from file CCD_Plugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    problem = ensure_plotly()
    if problem is not None:
        QMessageBox.critical(
            None,
            "CCD-Plugin: Error loading",
            problem,
            QMessageBox.StandardButton.Ok,
        )

    # Register icons under :/plugins/CCD_Plugin/ before the plugin class is imported
    from . import resources  # noqa: F401
    from .CCD_Plugin import CCD_Plugin

    return CCD_Plugin(iface)
