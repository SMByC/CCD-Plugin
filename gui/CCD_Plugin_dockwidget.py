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

with the collaboration of Daniel Moraes <moraesd90@gmail.com>

"""
import os

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtCore import QUrl, pyqtSignal, Qt, QDate
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsPointXY
from qgis.gui import QgsMapTool, QgsMapToolPan, QgsVertexMarker
from qgis.utils import iface

try:
    from qgis.PyQt.QtWebKit import QWebSettings

    # This loads your .ui file so that PyQt can populate your plugin with the elements from Qt Designer
    plugin_folder = os.path.dirname(os.path.dirname(__file__))
    FORM_CLASS, _ = uic.loadUiType(os.path.join(plugin_folder, 'ui', 'CCD_Plugin_dockwidget_QWebView.ui'))

except ImportError:
    # Qt without webkit
    msg = "CCD-Plugin needs QtWebKit in your QT/Qgis installation. See the options here:\n\n" \
          "https://github.com/SMByC/CCD-Plugin#installation"
    QMessageBox.critical(None, 'CCD-Plugin: Error loading', msg, QMessageBox.Ok)
    # raise Qgis error
    raise ImportError(msg)


from CCD_Plugin.core.ccd_process import compute_ccd
from CCD_Plugin.core.plot import generate_plot
from CCD_Plugin.utils.system_utils import wait_process


class CCD_PluginDockWidget(QtWidgets.QDockWidget, FORM_CLASS):
    closingPlugin = pyqtSignal()

    def __init__(self, parent=None):
        """Constructor."""
        super(CCD_PluginDockWidget, self).__init__(parent)
        # Set up the user interface from Designer through FORM_CLASS.
        # After self.setupUi() you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.setupUi(self)
        self.setup_gui()

    def setup_gui(self):
        # select swir1 band by default
        self.band_or_index.setCurrentIndex(4)
        # set the collection to 2 by default
        self.box_dataset.setCurrentIndex(1)
        # set break point bands/indices
        self.box_breakpoint_bands.addItems(['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2', 'NDVI', 'NBR', 'EVI',
                                           'EVI2', 'BRIGHTNESS', 'GREENNESS', 'WETNESS'])
        self.box_breakpoint_bands.setCheckedItems(['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2'])
        # set the current date
        self.end_date.setDate(QDate.currentDate())
        # set action center on point
        self.btm_center_on_point.clicked.connect(self.center_on_point)
        # set action when change the band or index repaint the plot
        self.band_or_index.currentIndexChanged.connect(lambda: self.repaint_plot())

        self.default_point_tool = QgsMapToolPan(iface.mapCanvas())
        iface.mapCanvas().setMapTool(self.default_point_tool, clean=True)

        self.pick_on_map.clicked.connect(self.coordinates_from_map)
        self.generate_button.clicked.connect(lambda: self.new_plot())

        # plot web view
        plot_view_settings = self.plot_webview.settings()
        plot_view_settings.setAttribute(QWebSettings.WebGLEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.Accelerated2dCanvasEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.PluginsEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.DnsPrefetchEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.XSSAuditingEnabled, True)
        plot_view_settings.setAttribute(QWebSettings.JavascriptEnabled, True)
        # define the zoom factor based on the dpi
        dpi = iface.mapCanvas().mapSettings().outputDpi()
        zoom_factor = dpi/96 - 0.4
        zoom_factor = 1 if zoom_factor < 1 else zoom_factor
        self.plot_webview.setZoomFactor(zoom_factor)

    def closeEvent(self, event):
        # close
        self.closingPlugin.emit()
        event.accept()

    def coordinates_from_map(self):
        # set the map tool and actions
        iface.mapCanvas().setMapTool(PickerCoordsOnMap(self), clean=True)

    def get_config_from_widget(self):
        # get the coordinates
        lon = self.longitude.value()
        lat = self.latitude.value()
        coords = (lon, lat)
        # get the date range
        start_date = self.start_date.date().toString("yyyy-MM-dd")
        end_date = self.end_date.date().toString("yyyy-MM-dd")
        date_range = (start_date, end_date)
        # get days of year range
        start_doy = self.start_doy.value()
        end_doy = self.end_doy.value()
        doy_range = (start_doy, end_doy)
        # get dataset selected
        dataset = self.box_dataset.currentText()
        # get band_or_index to plot
        band_or_index = self.band_or_index.currentText()
        # get breakpoint bands (detection bands)
        breakpoint_bands = self.box_breakpoint_bands.checkedItems()

        return coords, date_range, doy_range, dataset, band_or_index, breakpoint_bands


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

        # get the config from the widget
        coords, date_range, doy_range, dataset, band_or_index, breakpoint_bands = self.get_config_from_widget()
        #use default parameters while parameter setting option is not implemented
        tmask, numObs, chi, minYears, lda = [None, 6, 0.99, 1.33, 200]
        results  = compute_ccd(coords, date_range, doy_range, dataset, breakpoint_bands, tmask, numObs, chi, minYears, lda)
                    
        if not results:
            return
        ccdc_result_info, timeseries = results
        html_file = generate_plot(ccdc_result_info, timeseries, date_range, band_or_index, CCD_Plugin.tmp_dir)
        self.plot_webview.load(QUrl.fromLocalFile(html_file))

    @wait_process
    def repaint_plot(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from CCD_Plugin.core.ccd_process import ccd_results
        if not ccd_results:
            return
        # get the config from the widget
        coords, date_range, doy_range, dataset, band_or_index, breakpoint_bands = self.get_config_from_widget()
        # check if ccd results are already computed
        if (coords, date_range, doy_range, dataset, tuple(breakpoint_bands)) in ccd_results:
            ccdc_result_info, timeseries = ccd_results[(coords, date_range, doy_range, dataset, tuple(breakpoint_bands))]
            html_file = generate_plot(ccdc_result_info, timeseries, date_range, band_or_index, CCD_Plugin.tmp_dir)
            self.plot_webview.load(QUrl.fromLocalFile(html_file))


    def center_on_point(self):
        # get the coordinates
        point = QgsPointXY(self.longitude.value(), self.latitude.value())
        # transform coordinates to map canvas
        crsSrc = QgsCoordinateReferenceSystem(4326)
        crsDest = iface.mapCanvas().mapSettings().destinationCrs()
        xform = QgsCoordinateTransform(crsSrc, crsDest, QgsProject.instance())
        point = xform.transform(point)
        # create a marker
        PickerCoordsOnMap.create_marker(point)
        # center on point
        iface.mapCanvas().setCenter(point)


class PickerCoordsOnMap(QgsMapTool):
    marker = None

    def __init__(self, widget):
        QgsMapTool.__init__(self, iface.mapCanvas())
        self.widget = widget

    @staticmethod
    def delete_marker():
        if PickerCoordsOnMap.marker:
            iface.mapCanvas().scene().removeItem(PickerCoordsOnMap.marker)
            PickerCoordsOnMap.marker = None

    @staticmethod
    def create_marker(point):
        # remove the previous marker
        PickerCoordsOnMap.delete_marker()
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

            self.widget.longitude.setValue(point.x())
            self.widget.latitude.setValue(point.y())

            # clean the plot
            self.widget.plot_webview.setHtml("")

            self.finish()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.finish()

    def finish(self):
        iface.mapCanvas().unsetMapTool(self)
        iface.mapCanvas().setMapTool(self.widget.default_point_tool)

