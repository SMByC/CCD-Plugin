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

with the collaboration of Daniel Moraes <moraesd90@gmail.com>

"""
import os
from collections import OrderedDict

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtGui import QColor, QDesktopServices
from qgis.PyQt.QtCore import QUrl, pyqtSignal, Qt, QDate
from qgis.PyQt.QtWidgets import QMessageBox, QFileDialog
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsPointXY, Qgis,
                       QgsApplication, QgsTask)
from qgis.gui import QgsMapTool, QgsVertexMarker
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
from CCD_Plugin.utils.system_utils import wait_process, error_handler
from CCD_Plugin.utils.config import get_plugin_config, restore_plugin_config
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
        self.config = None
        self.last_config = None

        self.setupUi(self)
        self.setup_gui()

    def setup_gui(self):
        # select swir1 band by default
        self.band_or_index_to_plot.setCurrentIndex(4)
        # disable the item "---"
        self.band_or_index_to_plot.model().item(6).setEnabled(False)
        # set the collection to 2 by default
        self.dataset.setCurrentIndex(1)
        # set break point bands/indices
        self.box_breakpoint_bands.addItems(['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2', 'NDVI', 'NBR', 'EVI',
                                           'EVI2', 'BRIGHTNESS', 'GREENNESS', 'WETNESS'])
        self.box_breakpoint_bands.setCheckedItems(['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2'])
        # set the current date
        self.end_date.setDate(QDate.currentDate())
        # set action center on point
        self.focus_on_the_coordinates.clicked.connect(self.show_ang_go_to_the_coordinates)
        # set action when change the band or index repaint the plot
        self.band_or_index_to_plot.currentIndexChanged.connect(lambda: self.repaint_plot())

        self.default_map_tools = [canvas.mapTool() for canvas in self.canvas]
        self.pick_on_map.clicked.connect(self.coordinates_from_map)
        self.delete_markers.clicked.connect(PickerCoordsOnMap.delete_markers)

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
        # dpi = self.canvas[0].mapSettings().outputDpi()
        # zoom_factor = dpi/96 - 0.15
        self.plot_webview.setZoomFactor(0.85)

        # advanced settings dialog
        self.advanced_settings = AdvancedSettings()
        self.btm_advanced_settings.clicked.connect(self.advanced_settings.show)

        # restore the plugin configuration from a yml file
        self.restore_configuration.clicked.connect(lambda: self.restore_plugin_from_yml())

        # save the plugin configuration to a yml file
        self.save_configuration.clicked.connect(lambda: self.save_plugin_to_yml())

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

    def coordinates_from_map(self, checked):
        if not checked:
            # finish, set default map tool to canvas
            for canvas, default_map_tool in zip(self.canvas, self.default_map_tools):
                canvas.setMapTool(default_map_tool, clean=True)
            self.pick_on_map.setChecked(False)
        else:
            # set the map tool to pick coordinates
            for canvas, default_map_tool in zip(self.canvas, self.default_map_tools):
                canvas.unsetMapTool(default_map_tool)
                canvas.setMapTool(PickerCoordsOnMap(self, canvas), clean=True)

    @error_handler
    def new_plot(self):
        ### before start the process
        # check import ee lib
        try:
            import ee
            ee.Initialize()
        except Exception as err:
            raise Exception("Error importing ee lib, check the installation or your internet connection|{}".format(err))

        # get the current configuration of the plugin
        config = get_plugin_config(self.id)

        # check if the plugin settings have changed compared to the last plot, except for the band_or_index_to_plot
        if self.last_config and self.last_config == OrderedDict((k, v) for k, v in config.items() if k != 'band_or_index_to_plot'):
            return

        self.clean_plot()
        self.generate_button.setEnabled(False)
        self.plot_webview.load(QUrl.fromLocalFile(os.path.join(plugin_folder, 'ui', 'loading.html')))

        ### start the process
        # perform CCD as a background task
        globals()['task'] = QgsTask.fromFunction("Compute CCD", self.compute_ccd,
                                                 on_finished=self.ccd_completed, config=config)
        QgsApplication.taskManager().addTask(globals()['task'])

        ### after finish the process
        self.generate_button.setEnabled(True)
        self.pick_on_map.setChecked(False)
        self.coordinates_from_map(False)

    @staticmethod
    def compute_ccd(task, config):
        ccdc_result_info, timeseries = compute_ccd(
            coords=(config['lon'], config['lat']),
            date_range=(config['start_date'], config['end_date']),
            doy_range=(config['start_doy'], config['end_doy']),
            dataset=config['dataset'],
            breakpoint_bands=config['breakpoint_bands'],
            tmask_bands=None,
            num_obs=config['num_obs'],
            chi_square=config['chi_square'],
            min_years=config['min_years'],
            lambda_lasso=config['lambda_lasso'])

        return config, ccdc_result_info, timeseries

    def ccd_completed(self, exception, result=None):
        if exception is None and result is not None:
            # CCD process completed successfully
            config, ccdc_result_info, timeseries = result

            if not ccdc_result_info['tBreak']:
                msg = "No enough data for this period to perform change detection, plotting only the observed values."
                self.MsgBar.clearWidgets()
                self.MsgBar.pushMessage("CCD-Plugin", msg, level=Qgis.Info, duration=10)

            self.html_file = generate_plot(self.id, ccdc_result_info, timeseries,
                                           (config['start_date'], config['end_date']),
                                           config['dataset'], config['band_or_index_to_plot'])

            self.plot_webview.load(QUrl.fromLocalFile(self.html_file))

            self.last_config = OrderedDict((k, v) for k, v in config.items() if k != 'band_or_index_to_plot')
        else:
            msg = "Error computing CCD: {}".format(exception)
            self.MsgBar.clearWidgets()
            self.MsgBar.pushMessage("CCD-Plugin", msg, level=Qgis.Warning, duration=10)
            self.plot_webview.setHtml("")

    @wait_process
    def repaint_plot(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from CCD_Plugin.core.ccd_process import ccd_results
        self.clean_plot()

        if not ccd_results:
            return

        # get the current configuration of the plugin
        config = get_plugin_config(self.id)
        coords = (config['lon'], config['lat'])
        date_range = (config['start_date'], config['end_date'])
        doy_range = (config['start_doy'], config['end_doy'])
        dataset = config['dataset']
        band_or_index_to_plot = config['band_or_index_to_plot']
        breakpoint_bands = tuple(config['breakpoint_bands'])

        # check if ccd results are already computed
        if (coords, date_range, doy_range, dataset, breakpoint_bands) in ccd_results:
            ccdc_result_info, timeseries = ccd_results[(coords, date_range, doy_range, dataset, breakpoint_bands)]
            self.html_file = generate_plot(self.id, ccdc_result_info, timeseries, date_range, dataset, band_or_index_to_plot)
            self.plot_webview.load(QUrl.fromLocalFile(self.html_file))

    @error_handler
    def restore_plugin_from_yml(self):
        """restore the configuration of the plugin from a yml file"""
        import yaml

        yml_path, _ = QFileDialog.getOpenFileName(self,
                                                  "Restore the CCD plugin configuration from a yml file",
                                                  "", "YAML Files (*.yaml);;All Files (*)")

        if yml_path == '' or not os.path.isfile(yml_path):
            return

        with open(yml_path, 'r') as stream:
            try:
                config = yaml.safe_load(stream)
            except yaml.YAMLError as err:
                raise Exception("Error reading the yml file to restore the CCD plugin, see more:|{}".format(err))

        try:
            restore_plugin_config(self.id, config)
        except Exception as err:
            raise Exception("Error restoring the configuration of the CCD plugin, see more:|{}".format(err))

    @error_handler
    def save_plugin_to_yml(self):
        """save the configuration of the plugin to a yml file"""
        import yaml

        def setup_yaml():
            """Keep dump ordered with orderedDict"""
            represent_dict_order = lambda self, data: self.represent_mapping('tag:yaml.org,2002:map',
                                                                             list(data.items()))
            yaml.add_representer(OrderedDict, represent_dict_order)

        setup_yaml()
        config = get_plugin_config(self.id)
        yml_path, _ = QFileDialog.getSaveFileName(self,
                                                  "Save the CCD plugin configuration to a yml file",
                                                  "", "YAML Files (*.yaml);;All Files (*)")
        if yml_path == '':
            return

        if yml_path:
            if not yml_path.endswith(".yaml"):
                yml_path += ".yaml"

        with open(yml_path, 'w') as stream:
            try:
                yaml.dump(config, stream, default_flow_style=False)
            except yaml.YAMLError as err:
                raise Exception("Error writing the yml file to save the CCD plugin, see more:|{}".format(err))

    def show_ang_go_to_the_coordinates(self):
        if PickerCoordsOnMap.marker_drawn["canvas"] is not None:
            canvas = PickerCoordsOnMap.marker_drawn["canvas"]
        else:
            canvas = self.canvas[0]
        # get the coordinates
        point = QgsPointXY(self.longitude.value(), self.latitude.value())
        # transform coordinates to map canvas
        crsSrc = QgsCoordinateReferenceSystem(4326)
        crsDest = canvas.mapSettings().destinationCrs()
        xform = QgsCoordinateTransform(crsSrc, crsDest, QgsProject.instance())
        point = xform.transform(point)
        # create a marker
        PickerCoordsOnMap(self, canvas).create_marker(point)
        canvas.setCenter(point)
        canvas.refresh()

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
        self.canvas.setFocus()

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

            if self.widget.auto_generate_plot.isChecked():
                self.widget.new_plot()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.finish()

    def finish(self):
        for canvas, default_map_tool in zip(self.widget.canvas, self.widget.default_map_tools):
            canvas.unsetMapTool(self)
            canvas.setMapTool(default_map_tool)

        self.widget.pick_on_map.setChecked(False)

