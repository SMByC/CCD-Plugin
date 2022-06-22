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
"""

import os

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import QUrl, pyqtSignal
from qgis.PyQt.QtWebKit import QWebSettings

from CCD_Plugin.core.ccd_process import compute_ccd
from CCD_Plugin.core.plot import generate_plot

# This loads your .ui file so that PyQt can populate your plugin with the elements from Qt Designer
plugin_folder = os.path.dirname(os.path.dirname(__file__))
FORM_CLASS, _ = uic.loadUiType(os.path.join(plugin_folder, 'ui', 'CCD_Plugin_dialog_base.ui'))


class CCD_PluginDialog(QtWidgets.QDialog, FORM_CLASS):
    closingPlugin = pyqtSignal()

    def __init__(self, parent=None):
        """Constructor."""
        super(CCD_PluginDialog, self).__init__(parent)
        # Set up the user interface from Designer through FORM_CLASS.
        # After self.setupUi() you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.setupUi(self)
        self.setup_gui()

        # plot web view
        plot_view_settings = self.plot_webview.settings()
        plot_view_settings.setAttribute(QWebSettings.WebGLEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.DeveloperExtrasEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.Accelerated2dCanvasEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.PluginsEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.DnsPrefetchEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.XSSAuditingEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.CSSGridLayoutEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.JavaEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.JavascriptEnabled, True)

    def setup_gui(self):
        self.generate_button.clicked.connect(self.new_plot)

    def closeEvent(self, event):
        # close
        self.closingPlugin.emit()
        event.accept()

    def new_plot(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin

        # Run everything
        coords = (-74.163, 2.5182)
        year_range = (1970, 2022)
        doy_range = (1, 365)
        collection = 1

        ccd_results, dates, band_data = compute_ccd(coords, year_range, doy_range, collection)

        html_file = generate_plot(ccd_results, dates, band_data, CCD_Plugin.tmp_dir)

        self.plot_webview.load(QUrl.fromLocalFile(html_file))