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

with the collaboration of Daniel Moraes <moraesd90@gmail.com>

"""

import os
from collections import OrderedDict
from typing import ClassVar

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsTask,
)
from qgis.gui import QgsMapTool, QgsVertexMarker
from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtCore import QDate, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDesktopServices, QPalette
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.utils import iface

plugin_folder = os.path.dirname(os.path.dirname(__file__))

# Try QWebEngine first (Qt6 / QGIS 4.x, and Qt5 with WebEngine), then fall back to QtWebKit (Qt5 / QGIS 3.x)
HAS_WEBENGINE = False
HAS_WEBKIT = False

try:
    try:
        # Qt6 moved QWebEngineSettings out of QtWebEngineWidgets; under Qt5 it is still there.
        # Import it from both, or a Qt5 QGIS that ships WebEngine but no WebKit - increasingly
        # common on Linux - would be told it has neither.
        from qgis.PyQt.QtWebEngineCore import QWebEngineSettings
    except ImportError:
        from qgis.PyQt.QtWebEngineWidgets import QWebEngineSettings
    from qgis.PyQt.QtWebEngineWidgets import QWebEngineView  # noqa: F401

    HAS_WEBENGINE = True
    # This .ui is loaded by both bindings, so its enums are written unscoped (Qt5 style):
    # PyQt6's uic accepts that form, while PyQt5's uic cannot parse the Qt6-scoped one.
    FORM_CLASS, _ = uic.loadUiType(os.path.join(plugin_folder, "ui", "CCD_Plugin_dockwidget_QWebEngine.ui"))
except ImportError:
    pass

if not HAS_WEBENGINE:
    try:
        from qgis.PyQt.QtWebKit import QWebSettings

        HAS_WEBKIT = True
        FORM_CLASS, _ = uic.loadUiType(os.path.join(plugin_folder, "ui", "CCD_Plugin_dockwidget_QWebView.ui"))
    except ImportError:
        pass

if not HAS_WEBENGINE and not HAS_WEBKIT:
    msg = (
        "CCD-Plugin needs QtWebEngine or QtWebKit in your Qt/QGIS installation. See the options here:\n\n"
        "https://github.com/SMByC/CCD-Plugin#installation"
    )
    QMessageBox.critical(None, "CCD-Plugin: Error loading", msg, QMessageBox.StandardButton.Ok)
    raise ImportError(msg)


from CCD_Plugin.core.ccd_process import (  # noqa: E402
    DEFAULT_BREAKPOINT_BANDS,
    compute_ccd,
    resolve_ccd_bands,
)
from CCD_Plugin.core.gee_common import CCD_BANDS  # noqa: E402
from CCD_Plugin.core.loading import loading_page_html  # noqa: E402
from CCD_Plugin.core.plot import PlotStyle, generate_plot  # noqa: E402
from CCD_Plugin.gui.advanced_settings import AdvancedSettings  # noqa: E402
from CCD_Plugin.utils.config import get_plugin_config, restore_plugin_config  # noqa: E402
from CCD_Plugin.utils.system_utils import error_handler, wait_process  # noqa: E402


def _relative_luminance(color: QColor) -> float:
    channels = (color.redF(), color.greenF(), color.blueF())
    linear = tuple(
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels
    )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _plot_style_from_palette(palette: QPalette) -> PlotStyle:
    background = palette.color(QPalette.ColorRole.Base)
    text = palette.color(QPalette.ColorRole.Text)
    return PlotStyle.DARK if _relative_luminance(background) < _relative_luminance(text) else PlotStyle.LIGHT


class CCD_PluginDockWidget(QtWidgets.QDockWidget, FORM_CLASS):
    closingPlugin = pyqtSignal()

    def __init__(self, id, canvas=None, parent=None):
        """Constructor."""
        super().__init__(parent)
        # Set up the user interface from Designer through FORM_CLASS.
        # After self.setupUi() you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.id = id
        self.canvas = canvas if canvas is not None else [iface.mapCanvas()]
        self.config = None
        self.last_config = None
        self.task = None
        self.map_tools = {}

        self.setupUi(self)
        self.plot_style = _plot_style_from_palette(self.palette())
        self.setup_gui()

    def setup_gui(self):
        # select swir1 band by default
        self.band_or_index_to_plot.setCurrentIndex(4)
        # disable the item "---"
        self.band_or_index_to_plot.model().item(6).setEnabled(False)
        # set the collection to Landsat C2 by default
        self.dataset.setCurrentIndex(0)
        # set break point bands/indices, from the schema every dataset is built to expose
        self.box_breakpoint_bands.addItems(list(CCD_BANDS))
        # Blue is deliberately left out: it is the band most affected by residual haze and aerosol,
        # and including it is a well known source of false breaks. Zhu & Woodcock (2014), the
        # gee-ccdc-tools toolkit and SEPAL all detect change on Green/Red/NIR/SWIR1/SWIR2 only.
        self.box_breakpoint_bands.setCheckedItems(list(DEFAULT_BREAKPOINT_BANDS))
        # set the current date
        self.end_date.setDate(QDate.currentDate())
        # set action center on point
        self.focus_on_the_coordinates.clicked.connect(self.show_and_go_to_the_coordinates)
        # set action when change the band or index repaint the plot
        self.band_or_index_to_plot.currentIndexChanged.connect(lambda: self.repaint_plot())

        self.default_map_tools = [canvas.mapTool() for canvas in self.canvas]
        self.pick_on_map.clicked.connect(self.setup_map_tool)
        self.delete_markers.clicked.connect(PickerCoordsOnMap.delete_markers)

        self.generate_button.clicked.connect(lambda: self.new_plot())

        # plot web view settings
        plot_view_settings = self.plot_webview.settings()
        if HAS_WEBENGINE:
            plot_view_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            plot_view_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            plot_view_settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        elif HAS_WEBKIT:
            plot_view_settings.setAttribute(QWebSettings.JavascriptEnabled, True)
            plot_view_settings.setAttribute(QWebSettings.WebGLEnabled, True)
            plot_view_settings.setAttribute(QWebSettings.Accelerated2dCanvasEnabled, True)
        self.plot_webview.setZoomFactor(0.85)
        self.plot_webview.urlChanged.connect(self._retain_plot_style)

        # advanced settings dialog
        self.advanced_settings = AdvancedSettings()
        self.btm_advanced_settings.clicked.connect(self.advanced_settings.show)

        # restore the plugin configuration from a yaml file
        self.restore_configuration.clicked.connect(lambda: self.restore_plugin_from_yaml())

        # save the plugin configuration to a yaml file
        self.save_configuration.clicked.connect(lambda: self.save_plugin_to_yaml())

        # open the current html file in the web browser
        self.html_file = None
        self.btm_open_web_browser.clicked.connect(self.open_plot_in_web_browser)

        # enable/disable days of year when start day or end day is changed
        self.start_date.dateChanged.connect(lambda: self.enable_disable_days_of_year())
        self.end_date.dateChanged.connect(lambda: self.enable_disable_days_of_year())

    def _retain_plot_style(self, url: QUrl) -> None:
        fragment = url.fragment()
        if fragment in (PlotStyle.LIGHT.value, PlotStyle.DARK.value):
            self.plot_style = PlotStyle(fragment)

    def enable_disable_days_of_year(self):
        # only enable the days of year if the date range is greater than 1 year
        start_date = self.start_date.date()
        end_date = self.end_date.date()
        self.advanced_settings.doy_widget.setEnabled(start_date.daysTo(end_date) >= 365)

    def closeEvent(self, event):
        # close
        self.closingPlugin.emit()
        event.accept()

    def picker_for(self, canvas):
        """The picker tool for `canvas`, built once.

        QgsMapTool parents itself to its canvas, so a fresh tool per toggle was never freed and
        they accumulated for the life of the session.
        """
        picker = self.map_tools.get(canvas)
        if picker is None:
            picker = PickerCoordsOnMap(self, canvas)
            self.map_tools[canvas] = picker
        return picker

    def release_map_tools(self):
        """Hand every canvas back its default tool and drop the cached pickers.

        Without this the picker stays the active tool after the dock goes away: the canvas still
        owns it, so map clicks keep running against an orphaned widget, and the reference chain
        canvas -> picker -> picker.widget keeps the whole dock alive.
        """
        for canvas, default_map_tool in zip(self.canvas, self.default_map_tools, strict=True):
            if isinstance(canvas.mapTool(), PickerCoordsOnMap):
                canvas.setMapTool(default_map_tool, clean=True)
        self.map_tools.clear()

    def setup_map_tool(self, checked):
        if checked:
            # set the map tool to pick coordinates
            for canvas, default_map_tool in zip(self.canvas, self.default_map_tools, strict=True):
                canvas.unsetMapTool(default_map_tool)
                canvas.setMapTool(self.picker_for(canvas), clean=True)
        else:
            # finish, set default map tool to canvas
            for canvas, default_map_tool in zip(self.canvas, self.default_map_tools, strict=True):
                canvas.setMapTool(default_map_tool, clean=True)

    @error_handler
    def new_plot(self):
        # before start the process
        # check import ee lib
        try:
            import ee

            ee.Initialize()
        except Exception as err:
            raise Exception(f"Error importing ee lib, check the installation or your internet connection|{err}")

        # get the current configuration of the plugin
        config = get_plugin_config(self.id)
        if not config:
            return

        # nothing but the plotted band changed since the last run, and that is redrawn from cache
        if self.last_config and self.settings_unchanged(config):
            # say so rather than returning silently, or the button just looks dead
            self.MsgBar.clearWidgets()
            self.MsgBar.pushMessage(
                "CCD-Plugin",
                "These settings already produced the current plot, nothing to recompute.",
                level=Qgis.MessageLevel.Info,
                duration=5,
            )
            # deliberately stays in pick mode: nothing was computed, so the user is most likely
            # still picking and should not have the tool taken away
            return

        self.start_ccd_task(config)

        # after finish the process
        self.finish_picking()

    def finish_picking(self):
        """Leave pick-on-map mode, if it is what started this run.

        Only a toggle *off*: a bare click() also fires when the user pressed Generate directly,
        which switched the canvas over to the picker instead of releasing it.
        """
        if self.pick_on_map.isChecked():
            self.pick_on_map.click()

    # Presentation and UI preferences: none of these reach compute_ccd, so a change to one must not
    # look like a settings change. auto_generate_plot especially - it is a plain checkbox, and
    # counting it here made toggling it force a full Earth Engine recomputation on the next run.
    NON_COMPUTATION_SETTINGS: ClassVar[frozenset] = frozenset(
        {"band_or_index_to_plot", "plot_style", "auto_generate_plot"}
    )

    @classmethod
    def comparable_settings(cls, config):
        """Everything that drives the computation, excluding presentation-only settings."""
        return OrderedDict((k, v) for k, v in config.items() if k not in cls.NON_COMPUTATION_SETTINGS)

    def settings_unchanged(self, config):
        """True when computation settings match the run that produced the current plot."""
        return self.last_config == self.comparable_settings(config)

    def start_ccd_task(self, config):
        """Run CCD for this configuration as a background task."""
        self.clean_plot()
        # the band combo starts runs too, so lock it as well or a second task can race the first
        self.generate_button.setEnabled(False)
        self.band_or_index_to_plot.setEnabled(False)
        self.plot_webview.setHtml(loading_page_html(self.plot_style))
        # Held on the instance, not in module globals: the plugin is multi-instance, and a second
        # dock starting a run would otherwise drop the only Python reference to the first one's task.
        self.task = QgsTask.fromFunction("Compute CCD", self.compute_ccd, on_finished=self.ccd_completed, config=config)
        QgsApplication.taskManager().addTask(self.task)

    @staticmethod
    def compute_ccd(task, config):
        ccdc_result_info, timeseries = compute_ccd(
            coords=(config["lon"], config["lat"]),
            date_range=(config["start_date"], config["end_date"]),
            doy_range=(config["start_doy"], config["end_doy"]),
            dataset=config["dataset"],
            breakpoint_bands=config["breakpoint_bands"],
            tmask_bands=None,
            num_obs=config["num_obs"],
            chi_square=config["chi_square"],
            min_years=config["min_years"],
            lambda_lasso=config["lambda_lasso"],
            cloud_filter=config["cloud_filter"],
            plot_band=config["band_or_index_to_plot"],
        )

        return config, ccdc_result_info, timeseries

    def ccd_completed(self, exception, result=None):
        if exception is None and result is not None:
            # CCD process completed successfully
            config, ccdc_result_info, timeseries = result

            notices = []
            # An empty tBreak means CCDC fitted no segment at all at this pixel, not merely that it
            # found no break. Read it defensively: reduceRegion omits the key entirely when the
            # pixel has no output, and this runs in the task callback where a raise is invisible.
            if not ccdc_result_info.get("tBreak"):
                notices.append(
                    "Not enough data for this period to fit the change detection model, "
                    "plotting only the observed values."
                )
            # Earth Engine rejects a CCDC call whose TMask bands are not also breakpoint bands, so
            # the selection may have been widened; that widens change detection too, so say it.
            ccd_bands, _ = resolve_ccd_bands(config["breakpoint_bands"])
            added = [band for band in ccd_bands if band not in config["breakpoint_bands"]]
            if added:
                notices.append(
                    f"{', '.join(added)} added to the breakpoint bands: CCDC requires the TMask bands "
                    "to be breakpoint bands, so change is also detected on them."
                )
            if notices:
                self.MsgBar.clearWidgets()
                self.MsgBar.pushMessage("CCD-Plugin", " ".join(notices), level=Qgis.MessageLevel.Info, duration=10)

            self.html_file = generate_plot(
                self.id,
                ccdc_result_info,
                timeseries,
                config["dataset"],
                config["band_or_index_to_plot"],
                style=self.plot_style,
            )

            self.plot_webview.load(QUrl.fromLocalFile(self.html_file))

            self.last_config = self.comparable_settings(config)
        else:
            msg = f"Error computing CCD: {exception}"
            self.MsgBar.clearWidgets()
            self.MsgBar.pushMessage("CCD-Plugin", msg, level=Qgis.MessageLevel.Warning, duration=10)
            self.plot_webview.setHtml("")
            # forget the last run so Generate can retry these very settings; otherwise new_plot
            # sees no change and returns without doing anything
            self.last_config = None

        # finish
        self.generate_button.setEnabled(True)
        self.band_or_index_to_plot.setEnabled(True)

    @wait_process
    def repaint_plot(self):
        from CCD_Plugin.core.ccd_process import ccd_results, lookup_result, make_cache_key, resolve_computed_indices

        if not ccd_results:
            return

        # get the current configuration of the plugin
        config = get_plugin_config(self.id)
        dataset = config["dataset"]
        band_or_index_to_plot = config["band_or_index_to_plot"]

        # check if ccd results are already computed
        key = make_cache_key(
            (config["lon"], config["lat"]),
            (config["start_date"], config["end_date"]),
            (config["start_doy"], config["end_doy"]),
            dataset,
            config["breakpoint_bands"],
            num_obs=config["num_obs"],
            chi_square=config["chi_square"],
            min_years=config["min_years"],
            lambda_lasso=config["lambda_lasso"],
            cloud_filter=config["cloud_filter"],
        )
        cached = lookup_result(key, resolve_computed_indices(config["breakpoint_bands"], band_or_index_to_plot))
        if cached is None:
            if self.last_config and self.settings_unchanged(config):
                # Only the plotted band differs, and it needs an index the last run did not build,
                # so recompute rather than making the user press Generate for a band switch.
                self.start_ccd_task(config)
            else:
                # Something else changed too. Recomputing here would silently apply settings the
                # user never confirmed, so leave the current plot up and say why nothing happened.
                msg = "The settings changed since the last run. Press Generate to recompute the CCD."
                self.MsgBar.clearWidgets()
                self.MsgBar.pushMessage("CCD-Plugin", msg, level=Qgis.MessageLevel.Info, duration=10)
            return

        self.clean_plot()
        ccdc_result_info, timeseries = cached
        self.html_file = generate_plot(
            self.id,
            ccdc_result_info,
            timeseries,
            dataset,
            band_or_index_to_plot,
            style=self.plot_style,
        )
        self.plot_webview.load(QUrl.fromLocalFile(self.html_file))

    @error_handler
    def restore_plugin_from_yaml(self):
        """restore the configuration of the plugin from a YAML file"""
        import yaml

        yaml_path, _ = QFileDialog.getOpenFileName(
            self, "Restore the CCD plugin configuration from a YAML file", "", "YAML Files (*.yaml);;All Files (*)"
        )

        if yaml_path == "" or not os.path.isfile(yaml_path):
            return

        with open(yaml_path) as stream:
            try:
                config = yaml.safe_load(stream)
            except Exception as err:
                raise Exception(f"Error reading the YAML file to restore the CCD plugin, see more:|{err}")

        # The band combo repaints on currentIndexChanged, and restore_plugin_config sets it before
        # the dates, DOY and advanced settings. Left connected, that repaint runs against a
        # half-restored configuration and reports the settings as changed. Restore first, then
        # repaint once, below.
        previous_band = self.band_or_index_to_plot.currentText()
        self.band_or_index_to_plot.blockSignals(True)
        try:
            style_changed = restore_plugin_config(self.id, config)
        except Exception as err:
            raise Exception(f"Error restoring the configuration of the CCD plugin, see more:|{err}")
        finally:
            self.band_or_index_to_plot.blockSignals(False)

        if style_changed or self.band_or_index_to_plot.currentText() != previous_band:
            self.repaint_plot()

    @error_handler
    def save_plugin_to_yaml(self):
        """save the configuration of the plugin to a YAML file"""
        import yaml

        def setup_yaml():
            """Keep dump ordered with orderedDict"""

            def represent_dict_order(self, data):
                return self.represent_mapping("tag:yaml.org,2002:map", list(data.items()))

            yaml.add_representer(OrderedDict, represent_dict_order)

        setup_yaml()
        config = get_plugin_config(self.id)
        yaml_path, _ = QFileDialog.getSaveFileName(
            self, "Save the CCD plugin configuration to a YAML file", "", "YAML Files (*.yaml);;All Files (*)"
        )
        if yaml_path == "":
            return

        if yaml_path:
            if not yaml_path.endswith(".yaml"):
                yaml_path += ".yaml"

        with open(yaml_path, "w") as stream:
            try:
                yaml.dump(config, stream, default_flow_style=False)
            except yaml.YAMLError as err:
                raise Exception(f"Error writing the YAML file to save the CCD plugin, see more:|{err}")

    def show_and_go_to_the_coordinates(self):
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
        # create a marker; drawing one needs no map tool, so do not build one just to place it
        PickerCoordsOnMap.create_marker(canvas, point)
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
    marker_drawn: ClassVar[dict] = {"marker": None, "canvas": None}

    def __init__(self, widget, canvas=None):
        self.widget = widget
        self.canvas = canvas if canvas is not None else iface.mapCanvas()
        super().__init__(self.canvas)

    def activate(self):
        """Take keyboard focus every time the tool is set, not just when it is built.

        The canvas only delivers keyPressEvent while it holds focus, so Escape stops leaving pick
        mode once focus has moved to the dock. The tool is now built once and reused, so grabbing
        focus in __init__ would do it only on the first activation.
        """
        super().activate()
        self.canvas.setFocus()

    @staticmethod
    def delete_markers():
        if PickerCoordsOnMap.marker_drawn["marker"] is not None:
            PickerCoordsOnMap.marker_drawn["canvas"].scene().removeItem(PickerCoordsOnMap.marker_drawn["marker"])
        PickerCoordsOnMap.marker_drawn = {"marker": None, "canvas": None}

    @classmethod
    def create_marker(cls, canvas, point):
        """Draw the single coordinate marker on `canvas`, replacing any previous one.

        A classmethod because placing a marker is independent of the map tool: building a tool
        just to draw one parented an extra QgsMapTool to the canvas on every call.
        """
        # remove the previous marker
        cls.delete_markers()
        # create a marker
        marker = QgsVertexMarker(canvas)
        marker.setCenter(point)
        marker.setColor(QColor("red"))
        marker.setIconSize(30)
        marker.setIconType(QgsVertexMarker.IconType.ICON_CROSS)
        marker.setPenWidth(3)
        cls.marker_drawn["marker"] = marker
        cls.marker_drawn["canvas"] = canvas

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
            self.create_marker(self.canvas, point)
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
        if event.key() == Qt.Key.Key_Escape:
            self.widget.pick_on_map.click()
