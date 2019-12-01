# -*- coding: utf-8 -*-
"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019 by Xavier Corredor Llano, SMByC
        email                : xcorredorl@ideam.gov.co
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
import platform
import site

if platform.system() == "Windows":
    extlib_path = 'extlibs_windows'
if platform.system() == "Darwin":
    extlib_path = 'extlibs_darwin'
if platform.system() == "Linux":
    extlib_path = 'extlibs_linux'

site.addsitedir(os.path.abspath(os.path.join(os.path.dirname(__file__), extlib_path)))


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load CCD_Plugin class from file CCD_Plugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    #
    from .CCD_Plugin import CCD_Plugin
    return CCD_Plugin(iface)
