# -*- coding: utf-8 -*-
"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019-2022 by Xavier Corredor Llano, SMByC
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
import ctypes
import importlib
import inspect
import os
import platform
import shutil
import site
import pkg_resources

from qgis.PyQt.QtWidgets import QMessageBox

from CCD_Plugin.utils import extralibs


def get_extlib_path():
    if platform.system() == "Windows":
        extlib_path = 'extlibs_windows'
    if platform.system() == "Darwin":
        extlib_path = 'extlibs_darwin'
    if platform.system() == "Linux":
        extlib_path = 'extlibs_linux'
    return os.path.abspath(os.path.join(os.path.dirname(__file__), extlib_path))


def unload_all_dlls(directory):
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".dll"):
                dll_path = os.path.join(root, file)
                try:
                    # Load the DLL
                    dll = ctypes.windll.LoadLibrary(dll_path)
                    # Unload the DLL
                    ctypes.windll.kernel32.FreeLibrary(dll._handle)
                except Exception as e:
                    print(f"Error removing {file}: {e}")


def check_dependencies():
    try:
        import ccd
        importlib.reload(ccd)

        # update the ccd library if using an old version
        if len(inspect.signature(ccd.detect).parameters) != 18:
            extra_libs_path = get_extlib_path()
            # remove the extra libs ignoring the errors
            if platform.system() == "Windows":
                unload_all_dlls(extra_libs_path)
            shutil.rmtree(extra_libs_path, ignore_errors=True)
            # show a message to the user to restart QGIS
            msg = "The ccd library requires restarting QGIS to load the update after installing it."
            QMessageBox.information(None, 'CCD-Plugin: Restart QGIS', msg, QMessageBox.Ok)

            return False

        return True
    except ImportError:
        return False



def pre_init_plugin():

    extra_libs_path = get_extlib_path()

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
