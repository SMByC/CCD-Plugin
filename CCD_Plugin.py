# -*- coding: utf-8 -*-
"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019-2024 by Xavier Corredor Llano, SMByC
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

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, Qt, QLocale
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

# Initialize Qt resources from file resources.py
from .resources import *
# Import the code for the widget
from CCD_Plugin.gui.CCD_Plugin_dockwidget import CCD_PluginDockWidget
import os.path


class CCD_Plugin:
    """QGIS Plugin Implementation."""
    inst = {}

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
        try:
            locale = QSettings().value('locale/userLocale', QLocale().name(), type=str)[0:2]
        except:
            locale = 'en'
        locale_path = os.path.join(self.plugin_dir, 'i18n', 'CCD_Plugin_{}.qm'.format(locale))

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.menu_name_plugin = self.tr("Continuous Change Detection Plugin")
        self.pluginIsActive = False
        self.widget = None
        self.tmp_dir = None

        # save the instance
        self.id = str(id(self))
        CCD_Plugin.inst[self.id] = self

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
            if self.widget is None:
                # Create the dockwidget (after translation) and keep reference
                self.widget = CCD_PluginDockWidget(self.id)

            # init tmp dir for all process and intermediate files
            if self.tmp_dir:
                self.removes_temporary_files()
            self.tmp_dir = tempfile.mkdtemp()

            # connect to provide cleanup on closing of dockwidget
            self.widget.closingPlugin.connect(self.onClosePlugin)

            # show the dockwidget
            # TODO: fix to allow choice of dock location
            self.iface.addDockWidget(Qt.BottomDockWidgetArea, self.widget)
            self.widget.show()

    #--------------------------------------------------------------------------

    def onClosePlugin(self):
        """Cleanup necessary items here when plugin is closed"""
        self.removes_temporary_files()

        # delete the marker
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import PickerCoordsOnMap
        PickerCoordsOnMap.delete_markers()

        # remove this statement if widget is to remain
        # for reuse if plugin is reopened
        # Commented next statement since it causes QGIS crashe
        # when closing the docked window:
        self.widget.close()
        self.widget = None

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

        if self.widget:
            self.iface.removeDockWidget(self.widget)
            # delete the widget
            del self.widget

    def removes_temporary_files(self):
        if not self.widget:
            return

        # clear CCD_Plugin.tmp_dir
        if self.tmp_dir and os.path.isdir(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.tmp_dir = None

