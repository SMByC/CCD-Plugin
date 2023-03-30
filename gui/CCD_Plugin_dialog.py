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
# pyccd
# https://code.usgs.gov/lcmap/pyccd


import os, sys

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtCore import QUrl, pyqtSignal, Qt, QDate, QCoreApplication
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
from qgis.gui import QgsMapTool, QgsMapToolPan, QgsVertexMarker
from qgis.utils import iface

try:
    from qgis.PyQt.QtWebKit import QWebSettings

    # This loads your .ui file so that PyQt can populate your plugin with the elements from Qt Designer
    plugin_folder = os.path.dirname(os.path.dirname(__file__))
    FORM_CLASS, _ = uic.loadUiType(os.path.join(plugin_folder, 'ui', 'CCD_Plugin_dialog_base_QWebView.ui'))

except ImportError:
    # QtWebEngine for QT >= 5.15 and 6
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QtWidgets.qApp = QtWidgets.QApplication(sys.argv)
    from PyQt5.QtWebEngineWidgets import QWebEngineSettings as QWebSettings

    # This loads your .ui file so that PyQt can populate your plugin with the elements from Qt Designer
    plugin_folder = os.path.dirname(os.path.dirname(__file__))
    FORM_CLASS, _ = uic.loadUiType(os.path.join(plugin_folder, 'ui', 'CCD_Plugin_dialog_base_QWebEngine.ui'))

from CCD_Plugin.core.ccd_process import compute_ccd
from CCD_Plugin.core.plot import generate_plot
from CCD_Plugin.utils.system_utils import wait_process


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
        plot_view_settings.setAttribute(QWebSettings.Accelerated2dCanvasEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.PluginsEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.DnsPrefetchEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.XSSAuditingEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.JavascriptEnabled, True)

    def setup_gui(self):
        # select swir1 band by default
        self.band.setCurrentIndex(4)
        # set the collection to 2 by default
        self.collection.setCurrentIndex(1)
        # set the current date
        self.end_date.setDate(QDate.currentDate())

        self.default_point_tool = QgsMapToolPan(iface.mapCanvas())
        iface.mapCanvas().setMapTool(self.default_point_tool, clean=True)

        self.pick_on_map.clicked.connect(self.coordinates_from_map)
        self.generate_button.clicked.connect(lambda: self.new_plot())

    def keyPressEvent(self, event):
        # ignore esc key for close the main dialog
        if not event.key() == Qt.Key_Escape:
            super(CCD_PluginDialog, self).keyPressEvent(event)

    def closeEvent(self, event):
        # close
        self.closingPlugin.emit()
        event.accept()

    def coordinates_from_map(self):
        # minimize the plugin dialog
        self.setWindowState(Qt.WindowMinimized)
        # raise qgis window
        qgis_window = iface.mainWindow()
        qgis_window.raise_()
        qgis_window.setWindowState(qgis_window.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        qgis_window.activateWindow()

        # set the map tool and actions
        iface.mapCanvas().setMapTool(PickerCoordsOnMap(self), clean=True)

    @wait_process
    def new_plot(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        # clean the plot
        self.plot_webview.setHtml("")

        # check import ee lib
        try:
            import ee
            ee.Initialize()
        except Exception as err:
            raise Exception("Error importing ee lib, check the installation or your internet connection|{}".format(err))

        # get the coordinates
        lon = self.longitude.value()
        lat = self.latitude.value()
        coords = [lon, lat]
        # get the date range
        start_date = self.start_date.date().toString("yyyy-MM-dd")
        end_date = self.end_date.date().toString("yyyy-MM-dd")
        date_range = [start_date, end_date]
        # get days of year range
        start_doy = self.start_doy.value()
        end_doy = self.end_doy.value()
        doy_range = [start_doy, end_doy]
        # get collection
        collection = int(self.collection.currentText()[-1])
        # get band
        band = self.band.currentText()

        results = compute_ccd(coords, date_range, doy_range, collection, band)
        if not results:
            return
        ccd_results, dates, band_data = results
        html_file = generate_plot(ccd_results, dates, band_data, band, CCD_Plugin.tmp_dir)
        self.plot_webview.load(QUrl.fromLocalFile(html_file))


class PickerCoordsOnMap(QgsMapTool):
    marker = None

    def __init__(self, dialog):
        QgsMapTool.__init__(self, iface.mapCanvas())
        self.dialog = dialog

    @staticmethod
    def delete_marker():
        if PickerCoordsOnMap.marker:
            iface.mapCanvas().scene().removeItem(PickerCoordsOnMap.marker)
            PickerCoordsOnMap.marker = None

    def create_marker(self, point):
        # remove the previous marker
        self.delete_marker()
        # create a marker
        marker = QgsVertexMarker(iface.mapCanvas())
        marker.setCenter(point)
        marker.setColor(QColor("red"))
        marker.setIconSize(25)
        marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
        marker.setPenWidth(4)
        PickerCoordsOnMap.marker = marker

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            point = iface.mapCanvas().getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
            self.create_marker(point)
            # transform coordinates to WGS84
            crsSrc = iface.mapCanvas().mapSettings().destinationCrs()
            crsDest = QgsCoordinateReferenceSystem(4326)
            xform = QgsCoordinateTransform(crsSrc, crsDest, QgsProject.instance())
            point = xform.transform(point)

            self.dialog.longitude.setValue(point.x())
            self.dialog.latitude.setValue(point.y())

            self.finish()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.finish()

    def finish(self):
        iface.mapCanvas().unsetMapTool(self)
        iface.mapCanvas().setMapTool(self.dialog.default_point_tool)
        self.dialog.raise_()
        self.dialog.setWindowState(self.dialog.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.dialog.activateWindow()

