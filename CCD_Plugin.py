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
"""

import os.path
import shutil
import tempfile
from typing import ClassVar

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QSettings, Qt, QTimer, QTranslator
from qgis.PyQt.QtGui import QAction, QIcon
from qgis.PyQt.QtWidgets import QWIDGETSIZE_MAX

# Import the code for the widget
from CCD_Plugin.gui.CCD_Plugin_dockwidget import CCD_PluginDockWidget


class CCD_Plugin:
    """QGIS Plugin Implementation."""

    inst: ClassVar[dict] = {}

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
            locale = QSettings().value("locale/userLocale", QLocale().name(), type=str)[0:2]
        except Exception:
            locale = "en"
        locale_path = os.path.join(self.plugin_dir, "i18n", f"CCD_Plugin_{locale}.qm")

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
        return QCoreApplication.translate("CCD_Plugin", message)

    def initGui(self):
        # Main widget menu
        # Create action that will start plugin configuration
        icon_path = ":/plugins/CCD_Plugin/icons/ccd_plugin.svg"
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

            if self.tmp_dir:
                self.removes_temporary_files()
            self.tmp_dir = tempfile.mkdtemp()

            # print "** STARTING CCD_Plugin"

            # dockwidget may not exist if:
            #    first run of plugin
            #    removed on close (see self.onClosePlugin method)
            widget = self.widget
            if widget is None:
                # Create the dockwidget (after translation) and keep reference
                widget = CCD_PluginDockWidget(self.id, self.tmp_dir)
                self.widget = widget

            # connect to provide cleanup on closing of dockwidget
            widget.closingPlugin.connect(self.onClosePlugin)

            # force initial minimum height
            target_height = widget.minimumSizeHint().height()
            widget.setMinimumHeight(target_height)
            widget.setMaximumHeight(target_height)

            # show the dockwidget
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, widget)
            widget.show()

            # allow resizing larger afterward
            QTimer.singleShot(100, lambda: widget.setMaximumHeight(QWIDGETSIZE_MAX))

    # --------------------------------------------------------------------------

    def onClosePlugin(self):
        """Cleanup necessary items here when plugin is closed"""
        widget = self.widget
        if widget is None:
            return
        widget.dispose()
        self.removes_temporary_files()

        # delete the marker
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import PickerCoordsOnMap

        PickerCoordsOnMap.delete_markers()

        # give the canvases their default tool back before the dock goes away
        widget.release_map_tools()

        # remove this statement if widget is to remain
        # for reuse if plugin is reopened
        # Commented next statement since it causes QGIS crashe
        # when closing the docked window:
        widget.close()
        self.widget = None

        # reset some variables
        self.pluginIsActive = False

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import PickerCoordsOnMap

        PickerCoordsOnMap.delete_markers()
        if self.widget:
            self.widget.dispose()
        self.removes_temporary_files()
        # Remove the plugin item and icon
        self.iface.removePluginMenu(self.menu_name_plugin, self.dockable_action)
        self.iface.removeToolBarIcon(self.dockable_action)

        if self.widget:
            # hand the canvases back their default tool, and drop the cached pickers: each one
            # holds a reference to this widget and is owned by its canvas, so leaving them in
            # place keeps the dock reachable no matter what happens below
            self.widget.release_map_tools()
            self.iface.removeDockWidget(self.widget)
            # Drop the reference rather than deleteLater(): a CCD task still in flight holds the
            # widget alive through its on_finished bound method, and deleting the C++ object out
            # from under that callback turns a late finish into a RuntimeError. Releasing this
            # reference lets it be collected once nothing else refers to it.
            self.widget = None
        self.pluginIsActive = False
        CCD_Plugin.inst.pop(self.id, None)

    def removes_temporary_files(self):
        # the CCD cache holds the whole time series and coefficient set per entry, and the module
        # stays imported after a plugin reload, so it has to be emptied explicitly
        from CCD_Plugin.core.ccd_process import clear_results_cache

        clear_results_cache()

        # clear CCD_Plugin.tmp_dir
        if self.tmp_dir and os.path.isdir(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.tmp_dir = None
