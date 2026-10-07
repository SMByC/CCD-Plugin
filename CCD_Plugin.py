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
from typing import ClassVar

from qgis.PyQt import sip
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
        self.widget = None
        self.tmp_dir = None
        # the temporary directory get_plugin_tmp_dir created, as opposed to one an embedding plugin handed in
        self.created_tmp_dir = None

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
        """Show the dock, building it the first time.

        One dock for the life of the plugin, hidden when closed and shown again here. A dock built
        per opening stayed parented to the main window after its close, listed under the Panels
        menu: reopened from there and closed, it closed the current dock through its own
        closingPlugin connection, and each one kept its pickers and its Advanced dialog alive.
        """
        widget = self.widget
        if widget is None:
            # the plot directory is read late and created on demand, see get_plugin_tmp_dir
            widget = CCD_PluginDockWidget(self.id)
            self.widget = widget
            # connect to provide cleanup on closing of dockwidget
            widget.closingPlugin.connect(self.onClosePlugin)

            # force initial minimum height
            target_height = widget.minimumSizeHint().height()
            widget.setMinimumHeight(target_height)
            widget.setMaximumHeight(target_height)
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, widget)

            # allow resizing larger afterward
            QTimer.singleShot(100, lambda: None if sip.isdeleted(widget) else widget.setMaximumHeight(QWIDGETSIZE_MAX))

        widget.show()
        widget.raise_()

    # --------------------------------------------------------------------------

    def onClosePlugin(self):
        """The dock was closed: its run, plot, pick mode and marker are already gone (dispose).

        The dock stays, hidden, and is shown again by run(); only the pickers are let go.
        """
        if self.widget is not None:
            self.widget.release_map_tools()

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        widget, self.widget = self.widget, None
        if widget is not None and not sip.isdeleted(widget):
            widget.closingPlugin.disconnect(self.onClosePlugin)
            widget.dispose()
            # hand the canvases back their tool and delete the pickers: each one is owned by its
            # canvas and holds a reference to this widget
            widget.release_map_tools()
            self.iface.removeDockWidget(widget)
            # Deleting it is safe with a run still in flight: the task only holds a weak reference
            # to the dock, and dispose() disowned the task, so its completion is ignored.
            widget.deleteLater()
        self.removes_temporary_files()
        # the CCD cache holds the time series and coefficients of the last runs, and the module
        # stays imported after a plugin reload, so it has to be emptied explicitly
        from CCD_Plugin.core.ccd_process import clear_results_cache

        clear_results_cache()
        # Remove the plugin item and icon
        self.iface.removePluginMenu(self.menu_name_plugin, self.dockable_action)
        self.iface.removeToolBarIcon(self.dockable_action)
        self.dockable_action.deleteLater()
        CCD_Plugin.inst.pop(self.id, None)

    def release(self):
        """End an instance another plugin embedded: the counterpart of unload for one that never
        ran initGui. Stops its run, hands its canvases back their tools and forgets the instance.

        Its plot files go with dispose(); a temporary directory is removed only if it was created
        here, whatever tmp_dir is now: ThRasE hands in its own, shared with the rest of its files.
        """
        widget, self.widget = self.widget, None
        if widget is not None and not sip.isdeleted(widget):
            widget.dispose()
            widget.release_map_tools()
        created, self.created_tmp_dir = self.created_tmp_dir, None
        if created:
            shutil.rmtree(created, ignore_errors=True)
            if self.tmp_dir == created:
                self.tmp_dir = None
        CCD_Plugin.inst.pop(self.id, None)

    def removes_temporary_files(self):
        """Remove this instance's temporary directory, the plots written for its dock.

        The results cache is shared by every instance, the plugin's own dock and the ones other
        plugins embed, so it is left to unload.
        """
        for directory in {self.tmp_dir, self.created_tmp_dir} - {None}:
            shutil.rmtree(directory, ignore_errors=True)
        self.tmp_dir = None
        self.created_tmp_dir = None
