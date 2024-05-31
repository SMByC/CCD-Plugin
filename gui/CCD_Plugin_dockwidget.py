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
from qgis.PyQt.QtGui import QColor, QDesktopServices
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
from CCD_Plugin.gui.advanced_settings import AdvancedSettings


class CCD_PluginDockWidget(QtWidgets.QDockWidget, FORM_CLASS):
    closingPlugin = pyqtSignal()

    def __init__(self, id, canvas=None, parent=None):
        """Constructor."""
        super(CCD_PluginDockWidget, self).__init__(parent)
        # Set up the user interface from Designer through FORM_CLASS.
        # After self.setupUi() you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.id = id
        self.canvas = canvas if canvas is not None else [iface.mapCanvas()]
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

        self.default_map_tools = [canvas.mapTool() for canvas in self.canvas]

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
        dpi = self.canvas[0].mapSettings().outputDpi()
        zoom_factor = dpi/96 - 0.4
        zoom_factor = 1 if zoom_factor < 1 else zoom_factor
        self.plot_webview.setZoomFactor(zoom_factor)

        # advanced settings dialog
        self.advanced_settings = AdvancedSettings()
        self.btm_advanced_settings.clicked.connect(self.advanced_settings.show)

        # open the current html file in the web browser
        self.html_file = None
        self.btm_open_web_browser.clicked.connect(self.open_plot_in_web_browser)

        # enable/disable days of year when start day or end day is changed
        self.start_date.dateChanged.connect(lambda: self.enable_disable_days_of_year())
        self.end_date.dateChanged.connect(lambda: self.enable_disable_days_of_year())

    def enable_disable_days_of_year(self):
        # only enable the days of year if the date range is greater than 1 year
        start_date = self.start_date.date()
        end_date = self.end_date.date()
        if start_date.daysTo(end_date) >= 365:
            self.start_doy.setEnabled(True)
            self.end_doy.setEnabled(True)
        else:
            self.start_doy.setEnabled(False)
            self.end_doy.setEnabled(False)

    def closeEvent(self, event):
        # close
        self.closingPlugin.emit()
        event.accept()

    def coordinates_from_map(self):
        # set the map tool and actions
        for canvas, default_map_tool in zip(self.canvas, self.default_map_tools):
            canvas.unsetMapTool(default_map_tool)
            canvas.setMapTool(PickerCoordsOnMap(self, canvas), clean=True)

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
        if self.start_doy.isEnabled() and self.end_doy.isEnabled():
            start_doy = self.start_doy.value()
            end_doy = self.end_doy.value()
            doy_range = (start_doy, end_doy)
        else:
            doy_range = (1, 365)  # by default
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
        self.clean_plot()

        # check import ee lib
        try:
            import ee
            ee.Initialize()
        except Exception as err:
            raise Exception("Error importing ee lib, check the installation or your internet connection|{}".format(err))

        # get the config from the widget
        coords, date_range, doy_range, dataset, band_or_index, breakpoint_bands = self.get_config_from_widget()
        # get the advanced settings from the dialog
        numObs, chi, minYears, lda = self.advanced_settings.get_config_from_dialog()

        results  = compute_ccd(coords, date_range, doy_range, dataset, breakpoint_bands, None, numObs, chi, minYears, lda)
                    
        if not results:
            return
        ccdc_result_info, timeseries = results
        self.html_file = generate_plot(self.id, ccdc_result_info, timeseries, date_range, dataset, band_or_index)
        self.plot_webview.load(QUrl.fromLocalFile(self.html_file))

    @wait_process
    def repaint_plot(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from CCD_Plugin.core.ccd_process import ccd_results
        self.clean_plot()

        if not ccd_results:
            return

        # get the config from the widget
        coords, date_range, doy_range, dataset, band_or_index, breakpoint_bands = self.get_config_from_widget()
        # check if ccd results are already computed
        if (coords, date_range, doy_range, dataset, tuple(breakpoint_bands)) in ccd_results:
            ccdc_result_info, timeseries = ccd_results[(coords, date_range, doy_range, dataset, tuple(breakpoint_bands))]
            self.html_file = generate_plot(self.id, ccdc_result_info, timeseries, date_range, dataset, band_or_index)
            self.plot_webview.load(QUrl.fromLocalFile(self.html_file))

    def center_on_point(self):
        if PickerCoordsOnMap.marker_drawn["canvas"] is None:
            return
        # get the coordinates
        point = QgsPointXY(self.longitude.value(), self.latitude.value())
        # transform coordinates to map canvas
        crsSrc = QgsCoordinateReferenceSystem(4326)
        crsDest = PickerCoordsOnMap.marker_drawn["canvas"].mapSettings().destinationCrs()
        xform = QgsCoordinateTransform(crsSrc, crsDest, QgsProject.instance())
        point = xform.transform(point)
        # center on point
        PickerCoordsOnMap.marker_drawn["canvas"].setCenter(point)

    def clean_plot(self):
        if self.html_file and os.path.exists(self.html_file):
            os.remove(self.html_file)
        self.plot_webview.setHtml("")
        self.html_file = None

    def open_plot_in_web_browser(self):
        # TODO: generate and open mosaic of all bands and indices in the web browser
        if self.html_file and os.path.exists(self.html_file):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.html_file))


class PickerCoordsOnMap(QgsMapTool):
    marker_drawn = {"marker": None, "canvas": None}

    def __init__(self, widget, canvas=None):
        self.widget = widget
        self.canvas = canvas if canvas is not None else iface.mapCanvas()
        QgsMapTool.__init__(self, self.canvas)

    @staticmethod
    def delete_markers():
        if PickerCoordsOnMap.marker_drawn["marker"] is not None:
            PickerCoordsOnMap.marker_drawn["canvas"].scene().removeItem(PickerCoordsOnMap.marker_drawn["marker"])
                
        PickerCoordsOnMap.marker_drawn = {"marker": None, "canvas": None}

    def create_marker(self, point):
        # remove the previous marker
        self.delete_markers()
        # create a marker
        marker = QgsVertexMarker(self.canvas)
        marker.setCenter(point)
        marker.setColor(QColor("red"))
        marker.setIconSize(25)
        marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
        marker.setPenWidth(4)
        PickerCoordsOnMap.marker_drawn["marker"] = marker
        PickerCoordsOnMap.marker_drawn["canvas"] = self.canvas

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
            self.create_marker(point)
            # transform coordinates to WGS84
            crsSrc = self.canvas.mapSettings().destinationCrs()
            crsDest = QgsCoordinateReferenceSystem(4326)
            xform = QgsCoordinateTransform(crsSrc, crsDest, QgsProject.instance())
            point = xform.transform(point)

            self.widget.longitude.setValue(point.x())
            self.widget.latitude.setValue(point.y())

            self.finish()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.finish()

    def finish(self):
        for canvas, default_map_tool in zip(self.widget.canvas, self.widget.default_map_tools):
            canvas.unsetMapTool(self)
            canvas.setMapTool(default_map_tool)

        self.widget.pick_on_map.setChecked(False)

