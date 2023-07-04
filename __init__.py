# -*- coding: utf-8 -*-
"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019-2023 by Xavier Corredor Llano, SMByC
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
import os
import site
import pkg_resources

from qgis.PyQt.QtWidgets import QMessageBox

from CCD_Plugin.utils import extralibs


def check_dependencies():
    try:
        import plotly
        return True
    except ImportError:
        return False


def pre_init_plugin():

    extra_libs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'extlibs'))

    if os.path.isdir(extra_libs_path):
        # add to python path
        site.addsitedir(extra_libs_path)
        # pkg_resources doesn't listen to changes on sys.path.
        pkg_resources.working_set.add_entry(extra_libs_path)


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load CCD_Plugin class from file CCD_Plugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    # load extra python dependencies
    pre_init_plugin()

    if not check_dependencies():
        # install extra python dependencies
        extralibs.install()
        # load extra python dependencies
        pre_init_plugin()

        if not check_dependencies():
            msg = "Error loading libraries for CCD-Plugin. " \
                  "Read the install instructions here:\n\n" \
                  "https://github.com/SMByC/CCD-Plugin#installation"
            QMessageBox.critical(None, 'CCD-Plugin: Error loading', msg, QMessageBox.Ok)

    from .CCD_Plugin import CCD_Plugin
    return CCD_Plugin(iface)
