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
"""
import shutil
import tempfile

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

# Initialize Qt resources from file resources.py
from .resources import *
# Import the code for the widget
from CCD_Plugin.gui.CCD_Plugin_dockwidget import CCD_PluginDockWidget
import os.path


class CCD_Plugin:
    """QGIS Plugin Implementation."""
    widget = None
    tmp_dir = None

    def __init__(self, iface):
        """Constructor.

        :param iface: An interface instance that will be passed to this class
            which provides the hook by which you can manipulate the QGIS
            application at run time.
        :type iface: QgsInterface
        """
        # Save reference to the QGIS interface
        self.iface = iface
        # initialize plugin directory
        self.plugin_dir = os.path.dirname(__file__)
        # initialize locale
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(
            self.plugin_dir,
            'i18n',
            'CCD_Plugin_{}.qm'.format(locale))

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.menu_name_plugin = self.tr("Continuous Change Detection Plugin")
        self.pluginIsActive = False
        CCD_Plugin.widget = None

    # noinspection PyMethodMayBeStatic
    def tr(self, message):
        """Get the translation for a string using Qt translation API.

        We implement this ourselves since we do not inherit QObject.

        :param message: String for translation.
        :type message: str, QString

        :returns: Translated version of message.
        :rtype: QString
        """
        # noinspection PyTypeChecker,PyArgumentList,PyCallByClass
        return QCoreApplication.translate('CCD_Plugin', message)

    def initGui(self):
        ### Main widget menu
        # Create action that will start plugin configuration
        icon_path = ':/plugins/CCD_Plugin/icons/ccd_plugin.svg'
        self.dockable_action = QAction(QIcon(icon_path), "CCD_Plugin", self.iface.mainWindow())
        # connect the action to the run method
        self.dockable_action.triggered.connect(self.run)
        # Add toolbar button and menu item
        self.iface.addToolBarIcon(self.dockable_action)
        self.iface.addPluginToMenu(self.menu_name_plugin, self.dockable_action)

    def run(self):
        """Run method that loads and starts the plugin"""

        if not self.pluginIsActive:
            self.pluginIsActive = True

            # print "** STARTING CCD_Plugin"

            # dockwidget may not exist if:
            #    first run of plugin
            #    removed on close (see self.onClosePlugin method)
            if CCD_Plugin.widget == None:
                # Create the dockwidget (after translation) and keep reference
                CCD_Plugin.widget = CCD_PluginDockWidget()

            # init tmp dir for all process and intermediate files
            if CCD_Plugin.tmp_dir:
                self.removes_temporary_files()
            CCD_Plugin.tmp_dir = tempfile.mkdtemp()

            # connect to provide cleanup on closing of dockwidget
            CCD_Plugin.widget.closingPlugin.connect(self.onClosePlugin)

            # show the dockwidget
            # TODO: fix to allow choice of dock location
            self.iface.addDockWidget(Qt.BottomDockWidgetArea, CCD_Plugin.widget)
            CCD_Plugin.widget.show()

    #--------------------------------------------------------------------------

    def onClosePlugin(self):
        """Cleanup necessary items here when plugin is closed"""
        self.removes_temporary_files()

        # delete the marker
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import PickerCoordsOnMap
        PickerCoordsOnMap.delete_marker()

        # remove this statement if widget is to remain
        # for reuse if plugin is reopened
        # Commented next statement since it causes QGIS crashe
        # when closing the docked window:
        CCD_Plugin.widget.close()
        CCD_Plugin.widget = None

        # reset some variables
        self.pluginIsActive = False

        from qgis.utils import reloadPlugin
        reloadPlugin("CCD_Plugin - Thematic Raster Editor")

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        self.removes_temporary_files()
        # Remove the plugin menu item and icon
        self.iface.removePluginMenu(self.menu_name_plugin, self.dockable_action)
        self.iface.removeToolBarIcon(self.dockable_action)

        if CCD_Plugin.widget:
            self.iface.removeDockWidget(CCD_Plugin.widget)
            # delete the widget
            del CCD_Plugin.widget

    @staticmethod
    def removes_temporary_files():
        if not CCD_Plugin.widget:
            return

        # clear CCD_Plugin.tmp_dir
        if CCD_Plugin.tmp_dir and os.path.isdir(CCD_Plugin.tmp_dir):
            shutil.rmtree(CCD_Plugin.tmp_dir, ignore_errors=True)
        CCD_Plugin.tmp_dir = None

