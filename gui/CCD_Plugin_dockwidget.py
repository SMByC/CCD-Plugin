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

import math
import os
import time
import traceback
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsMessageLog,
    QgsPointXY,
    QgsProject,
    QgsTask,
)
from qgis.gui import QgsMapTool, QgsVertexMarker
from qgis.PyQt import QtWidgets, sip, uic
from qgis.PyQt.QtCore import QDate, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDesktopServices, QPalette
from qgis.PyQt.QtWebEngineCore import QWebEngineLoadingInfo, QWebEnginePage, QWebEngineSettings
from qgis.PyQt.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from qgis.PyQt.QtWidgets import QFileDialog
from qgis.utils import iface

plugin_folder = os.path.dirname(os.path.dirname(__file__))

FORM_CLASS, _ = uic.loadUiType(os.path.join(plugin_folder, "ui", "CCD_Plugin_dockwidget_QWebEngine.ui"))


from CCD_Plugin.core.ccd_process import (  # noqa: E402
    DEFAULT_BREAKPOINT_BANDS,
    compute_ccd,
    correlated_detection_bands,
    ensure_earth_engine_initialized,
    has_cached_results,
    lookup_result,
    make_cache_key,
    resolve_ccd_bands,
    resolve_computed_indices,
)
from CCD_Plugin.core.coordinates import COORDINATE_DECIMALS, normalize_longitude  # noqa: E402
from CCD_Plugin.core.gee_common import CCD_BANDS  # noqa: E402
from CCD_Plugin.core.lifecycle import PlotFileLifecycle, PlotLoadController, TaskLifecycle  # noqa: E402
from CCD_Plugin.core.loading import blank_page_html, loading_page_html  # noqa: E402
from CCD_Plugin.core.plot import PlotSpec, PlotStyle, generate_plot  # noqa: E402
from CCD_Plugin.gui.advanced_settings import AdvancedSettings  # noqa: E402
from CCD_Plugin.utils.config import get_plugin_config, get_plugin_tmp_dir, restore_plugin_config  # noqa: E402
from CCD_Plugin.utils.system_utils import error_handler, wait_process  # noqa: E402

WGS84 = "EPSG:4326"
# A crash this soon after reloading the plot the view last crashed on is that plot crashing it
# again; a later one has some other cause, and the plot is simply reloaded once more.
CRASH_REPEAT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class CrashReload:
    """A plot reloaded after the view crashed, and when."""

    path: Path
    at: float = field(default_factory=time.monotonic)

    def repeats(self, path) -> bool:
        """Whether a crash on `path` now is this plot crashing the view again."""
        return path == self.path and time.monotonic() - self.at < CRASH_REPEAT_SECONDS


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


def _to_map_point(canvas, longitude, latitude):
    """A geographic coordinate in the canvas CRS, or None where that CRS cannot represent it."""
    try:
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem(WGS84), canvas.mapSettings().destinationCrs(), QgsProject.instance()
        )
        point = transform.transform(QgsPointXY(longitude, latitude))
    except QgsCsException:
        return None
    return point if math.isfinite(point.x()) and math.isfinite(point.y()) else None


class CCD_PluginDockWidget(QtWidgets.QDockWidget, FORM_CLASS):
    closingPlugin = pyqtSignal()

    def __init__(self, id, plot_directory=None, canvas=None, parent=None):
        """Constructor.

        Other plugins (AcATaMa, ThRasE) embed this widget: they build it with `id`, `canvas` (every
        view canvas they have) and `parent`, register it in CCD_Plugin.inst, drive it through
        new_plot, setup_map_tool, pick_on_map, latitude/longitude and auto_generate_plot, and end it
        with close(). Keep those working.
        """
        super().__init__(parent)
        # Set up the user interface from Designer through FORM_CLASS.
        # After self.setupUi() you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.id = id
        self.canvas = canvas if canvas is not None else [iface.mapCanvas()]
        # Canvases handed in are an embedding plugin's views. Their tools are transient - ThRasE's
        # editing tools are switched off through their buttons without finishing - so what picking
        # hands back there is each view's own default, the tool it had when the widget was built:
        # restoring the tool active when picking began brought back an editing tool with its button
        # off, and the next click edited the raster. QGIS' main canvas gets back the tool in use
        # when picking began, the one its toolbar shows checked.
        self.default_map_tools = {view: view.mapTool() for view in self.canvas} if canvas is not None else None
        self.last_config = None
        self.task = None
        # the indices the run in progress builds: a band switch it can serve does not restart it
        self.task_indices = ()
        self.task_lifecycle = TaskLifecycle()
        self.plot_files = PlotFileLifecycle(
            plot_directory if plot_directory is not None else lambda: get_plugin_tmp_dir(self.id)
        )
        self.plot_loads = PlotLoadController()
        # configuration of the plot being computed or loaded, read only while plot_in_progress()
        self.pending_config = None
        # pick-on-map state: the picker built for each canvas, and the tool to hand each canvas back
        # when picking ends (see default_map_tools)
        self.map_tools = {}
        self.previous_map_tools = {}
        self.picking = False
        self.marker = CoordinateMarker()
        # the last reload after the view crashed, so a plot that keeps crashing is not reloaded forever
        self.crash_reload = None
        # the confirmed plot was taken off the view after crashing it repeatedly: Generate draws it again
        self.plot_hidden = False

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
        for coordinate in (self.latitude, self.longitude):
            coordinate.setDecimals(COORDINATE_DECIMALS)
        # set the current date
        self.end_date.setDate(QDate.currentDate())
        # set action center on point
        self.focus_on_the_coordinates.clicked.connect(self.show_and_go_to_the_coordinates)
        # set action when change the band or index repaint the plot
        self.band_or_index_to_plot.currentIndexChanged.connect(lambda: self.repaint_plot())

        # toggled rather than clicked: the plugins embedding the widget end pick mode with setChecked
        self.pick_on_map.toggled.connect(self.setup_map_tool)
        # this dock's marker only: an embedded widget's marker belongs to the plugin embedding it
        self.delete_markers.clicked.connect(self.marker.clear)
        # A picker deactivates in the middle of the canvas installing the tool replacing it, when
        # the canvas still reports the picker as its tool, so the check runs once that is done.
        self._pick_mode_check = QTimer(self)
        self._pick_mode_check.setSingleShot(True)
        self._pick_mode_check.setInterval(0)
        self._pick_mode_check.timeout.connect(self._end_pick_mode_if_replaced)

        self.generate_button.clicked.connect(lambda: self.new_plot())

        # plot web view settings
        plot_view_settings = self.plot_webview.settings()
        plot_view_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        plot_view_settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        plot_view_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        plot_view_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        # Its Back/Reload entries navigate the view on its own, to a plot file already deleted or
        # away from the one the plugin believes is on display; the plot has its own toolbar.
        self.plot_webview.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.plot_webview.setZoomFactor(0.85)
        self.plot_webview.urlChanged.connect(self._retain_plot_style)
        plot_page = self.plot_webview.page()
        plot_page.loadingChanged.connect(self._plot_loading_changed)
        plot_page.renderProcessTerminated.connect(self._render_process_terminated)

        # advanced settings dialog, parented so it goes with the dock
        self.advanced_settings = AdvancedSettings(self)
        self.btm_advanced_settings.clicked.connect(self.advanced_settings.show)

        # restore the plugin configuration from a yaml file
        self.restore_configuration.clicked.connect(lambda: self.restore_plugin_from_yaml())

        # save the plugin configuration to a yaml file
        self.save_configuration.clicked.connect(lambda: self.save_plugin_to_yaml())

        # open the current html file in the web browser
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

    def show_message(self, text, level=Qgis.MessageLevel.Info, duration=10):
        self.MsgBar.clearWidgets()
        self.MsgBar.pushMessage("CCD-Plugin", text, level=level, duration=duration)

    def closeEvent(self, event):
        # close
        self.dispose()
        self.closingPlugin.emit()
        event.accept()

    def _live_canvases(self):
        """The canvases still alive: an embedding plugin may destroy its views before the widget."""
        return [canvas for canvas in self.canvas if not sip.isdeleted(canvas)]

    def picker_for(self, canvas):
        """The picker tool for `canvas`, built once and reused until release_map_tools.

        QgsMapTool parents itself to its canvas, so a fresh tool per toggle was never freed and
        they accumulated for the life of the session.
        """
        picker = self.map_tools.get(canvas)
        if picker is None or sip.isdeleted(picker):
            picker = PickerCoordsOnMap(self, canvas)
            picker.deactivated.connect(self._pick_mode_check.start)
            self.map_tools[canvas] = picker
        return picker

    def setup_map_tool(self, checked):
        """Enter or leave pick-on-map mode on every canvas; the pick_on_map button drives it."""
        if checked:
            self._enter_pick_mode()
        else:
            self._leave_pick_mode()

    def _enter_pick_mode(self):
        self.picking = True
        for canvas in self._live_canvases():
            picker = self.picker_for(canvas)
            current = canvas.mapTool()
            if current is picker:
                continue
            # see default_map_tools: on QGIS' canvas, the tool in use right now is what picking hands
            # back - the one active when the dock was built was stale by then
            if self.default_map_tools is None:
                self.previous_map_tools[canvas] = current
            else:
                self.previous_map_tools[canvas] = self.default_map_tools.get(canvas)
            canvas.setMapTool(picker, clean=True)

    def _leave_pick_mode(self):
        """Hand each canvas still on the picker its tool back; a canvas another tool took keeps that one.

        The tool is the one recorded when picking began: on QGIS' canvas the tool then in use, on
        an embedding plugin's view the view's default (see default_map_tools). One deleted since,
        or none at all, leaves the canvas without a tool rather than on the picker.
        """
        self.picking = False
        previous_map_tools, self.previous_map_tools = self.previous_map_tools, {}
        for canvas in self._live_canvases():
            picker = self.map_tools.get(canvas)
            if picker is None or sip.isdeleted(picker) or canvas.mapTool() is not picker:
                continue
            previous = previous_map_tools.get(canvas)
            restored = False
            if previous is not None and not sip.isdeleted(previous):
                try:
                    canvas.setMapTool(previous, clean=True)
                    restored = True
                except RuntimeError:
                    pass
            if not restored:
                # setMapTool(None) is a no-op, which left the picker active once picking had ended
                canvas.unsetMapTool(picker)

    def _end_pick_mode_if_replaced(self):
        """Another tool replaced the picker on a canvas: picking is over, on every canvas."""
        if not self.picking:
            return
        if any(canvas.mapTool() is not self.map_tools.get(canvas) for canvas in self._live_canvases()):
            if self.pick_on_map.isChecked():
                # toggled -> setup_map_tool(False)
                self.pick_on_map.setChecked(False)
            else:
                self._leave_pick_mode()

    def release_map_tools(self):
        """Leave pick mode, handing every canvas back its tool, and delete the pickers.

        A picker is parented to its canvas, so it outlives the dock unless deleted here, and the
        reference chain canvas -> picker -> picker.widget would keep the whole dock alive.
        """
        self.pick_on_map.setChecked(False)
        self._leave_pick_mode()
        for picker in self.map_tools.values():
            if not sip.isdeleted(picker):
                picker.deleteLater()
        self.map_tools.clear()

    @error_handler
    def new_plot(self):
        # before start the process
        # check import ee lib; it is initialized in the background task, see compute_ccd
        try:
            import ee  # noqa: F401
        except Exception as err:
            raise Exception(f"Error importing ee lib, check the installation of the Google Earth Engine plugin|{err}")

        # get the current configuration of the plugin
        config = get_plugin_config(self.id)
        if not config:
            return

        # both dates are included, so a single day is a valid range; ISO dates order as strings
        if config["start_date"] > config["end_date"]:
            self.show_message("The start date is after the end date.", level=Qgis.MessageLevel.Warning)
            return

        if self.plot_in_progress() and self.same_plot(self.pending_config, config):
            # exactly this plot is already on its way, and restarting it would only delay it
            return

        # nothing but the plotted band changed since the last run, and that is redrawn from cache
        if self.last_config and self.settings_unchanged(config):
            if not self.plot_in_progress() and not self.plot_hidden:
                # say so rather than returning silently, or the button just looks dead
                self.show_message("These settings already produced the current plot, nothing to recompute.", duration=5)
                # deliberately stays in pick mode: nothing was computed, so the user is most likely
                # still picking and should not have the tool taken away
                return
            # A newer run is about to replace the plot these settings produced, or the plot was
            # taken off the view (plot_hidden). Returning here let that run win over this, the
            # latest request, or left the view empty, so draw this plot again.
            if not self.draw_cached_plot(config):
                self.start_ccd_task(config)
        # settings a recent run already computed are drawn from the cache, not computed again
        elif not self.draw_cached_plot(config):
            self.start_ccd_task(config)

        # after finish the process
        self.finish_picking()

    def finish_picking(self):
        """Leave pick-on-map mode, if it is what started this run."""
        if self.pick_on_map.isChecked():
            self.pick_on_map.setChecked(False)

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

    @classmethod
    def same_plot(cls, config, other):
        """True when both configurations draw the same plot: same computation and same band."""
        if config is None or other is None:
            return False
        return (
            cls.comparable_settings(config) == cls.comparable_settings(other)
            and config["band_or_index_to_plot"] == other["band_or_index_to_plot"]
        )

    def plot_in_progress(self):
        """True while a plot is being computed or loaded to replace the one on display."""
        return self.task is not None or self.plot_loads.pending is not None

    def start_ccd_task(self, config):
        """Run CCD for this configuration as a background task."""
        self.clean_plot()
        self.pending_config = config
        self.task_indices = resolve_computed_indices(config["breakpoint_bands"], config["band_or_index_to_plot"])
        # the band combo starts runs too, and switching it now would restart this one, so lock it too
        self.generate_button.setEnabled(False)
        self.band_or_index_to_plot.setEnabled(False)
        self.plot_webview.setHtml(loading_page_html(self.plot_style))
        # Held on the instance, not in module globals: the plugin is multi-instance, and a second
        # dock starting a run would otherwise drop the only Python reference to the first one's task.
        dock_ref = weakref.ref(self)
        task_holder = []

        def finished(exception, result=None):
            dock = dock_ref()
            # An embedding plugin can destroy its window, and the widget in it, without close():
            # its Python side outlives it through CCD_Plugin.inst, its Qt side does not.
            if dock is not None and not sip.isdeleted(dock):
                dock.ccd_completed(task_holder[0], exception, result)

        task = QgsTask.fromFunction(
            "Compute CCD",
            self.compute_ccd,
            on_finished=finished,
            config=config,
        )
        task_holder.append(task)
        self.task = task
        self.task_lifecycle.start(task)
        QgsApplication.taskManager().addTask(self.task)

    @staticmethod
    def compute_ccd(task, config):
        # here rather than before the task starts: the first initialization talks to Google and
        # would freeze QGIS while it does
        ensure_earth_engine_initialized()
        computed = compute_ccd(
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
            cancelled=task.isCanceled,
        )
        if computed is None or task.isCanceled():
            return None
        ccdc_result_info, timeseries = computed
        return config, ccdc_result_info, timeseries

    def ccd_completed(self, task, exception, result=None):
        if not self.task_lifecycle.finish(task):
            return
        self.task = None
        try:
            if exception is None and result is not None:
                computed_config, ccdc_result_info, timeseries = result
                config = self._plot_config_for(computed_config)
                notices = []
                if not ccdc_result_info.get("tBreak"):
                    notices.append(
                        "Not enough data for this period to fit the change detection model, "
                        "plotting only the observed values."
                    )
                ccd_bands, _ = resolve_ccd_bands(config["breakpoint_bands"])
                added = [band for band in ccd_bands if band not in config["breakpoint_bands"]]
                if added:
                    notices.append(
                        f"{', '.join(added)} added to the breakpoint bands: CCDC requires the TMask bands "
                        "to be breakpoint bands, so change is also detected on them."
                    )
                correlated = correlated_detection_bands(config["breakpoint_bands"])
                if correlated:
                    notices.append(
                        f"{', '.join(correlated)} repeat bands already used for change detection: correlated "
                        "bands make the chi-square test flag change more readily than its probability says."
                    )
                if notices:
                    self.show_message(" ".join(notices))

                self.load_plot(ccdc_result_info, timeseries, config)
            elif task.isCanceled():
                self.show_message("CCD computation cancelled.")
                self._restore_view()
            else:
                self.show_message(f"Error computing CCD: {exception}", level=Qgis.MessageLevel.Warning)
                self._restore_view()
        except Exception as error:
            # QGIS' task wrapper swallows whatever this callback raises, so report it here or the
            # failure is silent and the loading page stays up for good
            QgsMessageLog.logMessage(traceback.format_exc(), "CCD-Plugin", Qgis.MessageLevel.Critical)
            self.show_message(f"Error drawing the CCD plot: {error}", level=Qgis.MessageLevel.Warning)
            self._restore_view()
        finally:
            self.generate_button.setEnabled(True)
            self.band_or_index_to_plot.setEnabled(True)

    def _plot_config_for(self, computed_config):
        """The configuration to draw a finished run with.

        A band switched while the run computed - a configuration restore, or an embedding plugin -
        is honoured when the run built what it needs, rather than restarting it; see repaint_plot.
        """
        pending = self.pending_config
        if pending is not None and self.comparable_settings(pending) == self.comparable_settings(computed_config):
            return pending
        return computed_config

    def load_plot(self, ccdc_result_info, timeseries, config):
        """Write the plot of this configuration and load it to replace the one on display."""
        spec = PlotSpec(
            dataset=config["dataset"],
            band=config["band_or_index_to_plot"],
            longitude=float(config["lon"]),
            latitude=float(config["lat"]),
            doy_range=(config["start_doy"], config["end_doy"]),
        )
        pending_plot = generate_plot(
            ccdc_result_info,
            timeseries,
            spec,
            self.plot_files,
            style=self.plot_style,
        )
        self.plot_loads.begin(Path(pending_plot))
        self.pending_config = config
        self.plot_webview.load(QUrl.fromLocalFile(pending_plot))

    def draw_cached_plot(self, config):
        """Draw this configuration from the CCD cache, superseding any plot in progress.

        False, with nothing touched, when the cache cannot serve it.
        """
        key = make_cache_key(
            (config["lon"], config["lat"]),
            (config["start_date"], config["end_date"]),
            (config["start_doy"], config["end_doy"]),
            config["dataset"],
            config["breakpoint_bands"],
            num_obs=config["num_obs"],
            chi_square=config["chi_square"],
            min_years=config["min_years"],
            lambda_lasso=config["lambda_lasso"],
            cloud_filter=config["cloud_filter"],
        )
        cached = lookup_result(
            key, resolve_computed_indices(config["breakpoint_bands"], config["band_or_index_to_plot"])
        )
        if cached is None:
            return False
        # Without this a run still computing would land after the redraw and replace it
        self.clean_plot()
        ccdc_result_info, timeseries = cached
        try:
            self.load_plot(ccdc_result_info, timeseries, config)
        except Exception:
            # the run this superseded is already cancelled, so do not leave its loading page up
            self._restore_view()
            raise
        return True

    @wait_process
    def repaint_plot(self):
        if not has_cached_results() and not self.plot_in_progress():
            return

        # get the current configuration of the plugin
        config = get_plugin_config(self.id)
        if not config:
            return
        if self.draw_cached_plot(config):
            return

        # The run in progress computes exactly these settings and builds every index the new band
        # needs, so it is left to land with this band (see _plot_config_for) instead of restarted.
        if (
            self.task is not None
            and self.pending_config is not None
            and self.comparable_settings(self.pending_config) == self.comparable_settings(config)
            and set(resolve_computed_indices(config["breakpoint_bands"], config["band_or_index_to_plot"]))
            <= set(self.task_indices)
        ):
            self.pending_config = config
            return

        # The band switches against the latest run: the one in progress, or else the one on display.
        # Comparing with the plot on display while another run is in progress let that run land
        # later with the band it was started with.
        in_progress = self.plot_in_progress() and self.pending_config is not None
        last_run = self.comparable_settings(self.pending_config) if in_progress else self.last_config
        if last_run and last_run == self.comparable_settings(config):
            # Only the plotted band differs, and it needs an index the last run did not build,
            # so recompute rather than making the user press Generate for a band switch.
            self.start_ccd_task(config)
        else:
            # Something else changed too. Recomputing here would silently apply settings the
            # user never confirmed, so leave the current plot up and say why nothing happened.
            self.show_message("The settings changed since the last run. Press Generate to recompute the CCD.")

    @error_handler
    def restore_plugin_from_yaml(self):
        """restore the configuration of the plugin from a YAML file"""
        import yaml

        yaml_path, _ = QFileDialog.getOpenFileName(
            self, "Restore the CCD plugin configuration from a YAML file", "", "YAML Files (*.yaml);;All Files (*)"
        )

        if yaml_path == "" or not os.path.isfile(yaml_path):
            return

        with open(yaml_path, encoding="utf-8") as stream:
            try:
                config = yaml.safe_load(stream)
            except Exception as err:
                raise Exception(f"Error reading the YAML file to restore the CCD plugin, see more:|{err}")

        # Validated whole before anything is applied, so a bad file leaves the settings as they
        # were. restore_plugin_config blocks the band combo while it applies, so the repaint the
        # band change asks for runs once, below, against the complete configuration.
        previous_band = self.band_or_index_to_plot.currentText()
        try:
            style_changed = restore_plugin_config(self.id, config, strict=True)
        except Exception as err:
            raise Exception(f"Error restoring the configuration of the CCD plugin, see more:|{err}")

        if style_changed or self.band_or_index_to_plot.currentText() != previous_band:
            self.repaint_plot()

    @error_handler
    def save_plugin_to_yaml(self):
        """save the configuration of the plugin to a YAML file"""
        import yaml

        config = get_plugin_config(self.id)
        yaml_path, _ = QFileDialog.getSaveFileName(
            self, "Save the CCD plugin configuration to a YAML file", "", "YAML Files (*.yaml);;All Files (*)"
        )
        if not yaml_path:
            return
        if not yaml_path.endswith(".yaml"):
            yaml_path += ".yaml"

        with open(yaml_path, "w", encoding="utf-8") as stream:
            try:
                # a plain dict in insertion order, without registering a representer on the
                # yaml module every other plugin shares
                yaml.safe_dump(dict(config), stream, default_flow_style=False, sort_keys=False)
            except yaml.YAMLError as err:
                raise Exception(f"Error writing the YAML file to save the CCD plugin, see more:|{err}")

    def show_and_go_to_the_coordinates(self):
        canvases = self._live_canvases()
        if not canvases:
            return
        canvas = self.marker.canvas if self.marker.canvas in canvases else canvases[0]
        point = self.marker.show(canvas, self.longitude.value(), self.latitude.value())
        if point is None:
            self.show_message(
                "The coordinate cannot be shown in the coordinate system of the map.", level=Qgis.MessageLevel.Warning
            )
            return
        canvas.setCenter(point)
        canvas.refresh()

    def clean_plot(self):
        """Drop the plot in progress, computing or loading, so only what follows reaches the view."""
        if self.task_lifecycle.cancel():
            self.task = None
            # the cancelled task's completion is now ignored, and it is what releases these
            self.generate_button.setEnabled(True)
            self.band_or_index_to_plot.setEnabled(True)
        self.task_indices = ()
        pending = self.plot_files.pending_path
        if pending is not None:
            self.plot_files.rollback(pending)
        self.plot_loads.cancel()
        self.pending_config = None

    def _restore_view(self):
        """Put the confirmed plot back on display, or an empty view when there is none.

        Every way a new plot can fail ends here: the run failing or being cancelled, writing the
        plot failing, and the view failing, stopping or crashing while loading it. None of them may
        leave the loading page up.
        """
        pending = self.plot_files.pending_path
        if pending is not None:
            self.plot_files.rollback(pending)
        self.plot_loads.cancel()
        if self.task is None:
            self.pending_config = None
        active = self.plot_files.active_path
        if active is not None and active.exists():
            self.plot_hidden = False
            self.plot_webview.load(QUrl.fromLocalFile(str(active)))
        else:
            self.last_config = None
            self.plot_webview.setHtml(blank_page_html(self.plot_style))

    def _plot_loading_changed(self, info: QWebEngineLoadingInfo) -> None:
        pending_load = self.plot_loads.pending
        pending_path = self.plot_files.pending_path
        if pending_load is None or pending_path is None:
            return
        status = info.status()
        # A stopped load is as final as a failed one: left out, it kept the plot pending for good,
        # and Generate then refused to restart what it took for a plot still on its way.
        terminal_statuses = (
            QWebEngineLoadingInfo.LoadStatus.LoadSucceededStatus,
            QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus,
            QWebEngineLoadingInfo.LoadStatus.LoadStoppedStatus,
        )
        if status not in terminal_statuses:
            return
        path = Path(info.url().toLocalFile())
        succeeded = status == QWebEngineLoadingInfo.LoadStatus.LoadSucceededStatus
        resolution = self.plot_loads.resolve(
            pending_load.generation,
            path,
            succeeded=succeeded,
        )
        if resolution is None:
            return
        if resolution:
            self.plot_files.commit(pending_path)
            self.last_config = self.comparable_settings(self.pending_config)
            self.plot_hidden = False
            self.crash_reload = None
            return
        # a renderer crash fails the load with no error of its own, and has reported itself already
        if status == QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus and info.errorString():
            self.show_message(f"The plot could not be displayed: {info.errorString()}", level=Qgis.MessageLevel.Warning)
        self._restore_view()

    def _render_process_terminated(self, status, _exit_code) -> None:
        """The web view's renderer died: show again whatever was on display."""
        if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            return
        if self.task is not None:
            self.show_message("The plot view stopped unexpectedly and was reloaded.", level=Qgis.MessageLevel.Warning)
            self.plot_webview.setHtml(loading_page_html(self.plot_style))
            return
        active = self.plot_files.active_path
        if self.plot_loads.pending is not None:
            # it died loading the new plot, not on the one on display, which is put back
            outcome = "the previous one is shown again" if active is not None else "press Generate to try again"
            self.show_message(
                f"The plot view stopped while loading the new plot; {outcome}.", level=Qgis.MessageLevel.Warning
            )
        elif active is not None and self.crash_reload is not None and self.crash_reload.repeats(active):
            # It died again on the plot it was just reloaded with, so reloading it once more would
            # loop. Generate draws it again (see plot_hidden), the browser shows it as it is.
            self.crash_reload = None
            self.plot_hidden = True
            self.show_message(
                "The plot view keeps stopping on this plot. Press Generate to draw it again, "
                "or open it in the web browser.",
                level=Qgis.MessageLevel.Warning,
            )
            self.plot_webview.setHtml(blank_page_html(self.plot_style))
            return
        else:
            self.show_message("The plot view stopped unexpectedly and was reloaded.", level=Qgis.MessageLevel.Warning)
        self._restore_view()
        if self.plot_files.active_path is not None:
            self.crash_reload = CrashReload(self.plot_files.active_path)

    def dispose(self):
        """Drop whatever is in flight or on display: the run, the plot, pick mode and the marker.

        The widget stays usable afterwards: the plugin reuses its dock once it is closed, and the
        plugins embedding it reach this through close().
        """
        self.task_lifecycle.dispose()
        self.task = None
        self.task_indices = ()
        self.generate_button.setEnabled(True)
        self.band_or_index_to_plot.setEnabled(True)
        self.plot_loads.cancel()
        self.pending_config = None
        self.plot_files.clear()
        self.last_config = None
        self.plot_hidden = False
        self.crash_reload = None
        self.plot_webview.setHtml(blank_page_html(self.plot_style))
        self.advanced_settings.hide()
        self.pick_on_map.setChecked(False)
        self._leave_pick_mode()
        self.marker.clear()

    def open_plot_in_web_browser(self):
        # TODO: generate and open mosaic of all bands and indices in the web browser
        browser_path = self.plot_files.browser_path
        if browser_path is not None and browser_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(browser_path)))


class CoordinateMarker:
    """The red cross showing the analysed coordinate on one canvas.

    Kept as longitude/latitude and re-projected whenever the canvas CRS changes: the item itself
    holds map coordinates, so it stayed at the old numbers - somewhere else entirely - after the
    project CRS changed. It lets go of a canvas being destroyed, whose scene deletes the item.
    """

    _markers: ClassVar[weakref.WeakSet] = weakref.WeakSet()

    def __init__(self):
        self.canvas = None
        self.item = None
        self.longitude = None
        self.latitude = None
        CoordinateMarker._markers.add(self)

    @classmethod
    def clear_all(cls):
        for marker in list(cls._markers):
            marker.clear()

    def show(self, canvas, longitude, latitude, map_point=None):
        """Place the marker; where it landed in map coordinates, or None when it cannot be shown.

        `map_point` is where it was picked, kept as is: a click past the antimeridian of a
        geographic canvas has a longitude beyond 180 that the coordinate itself is wrapped from.
        """
        self.clear()
        point = QgsPointXY(map_point) if map_point is not None else _to_map_point(canvas, longitude, latitude)
        if point is None:
            return None
        item = QgsVertexMarker(canvas)
        item.setCenter(point)
        item.setColor(QColor("red"))
        item.setIconSize(30)
        item.setIconType(QgsVertexMarker.IconType.ICON_CROSS)
        item.setPenWidth(3)
        self.canvas, self.item = canvas, item
        self.longitude, self.latitude = longitude, latitude
        canvas.destinationCrsChanged.connect(self._reproject)
        canvas.destroyed.connect(self._forget)
        return point

    def clear(self):
        canvas, item = self.canvas, self.item
        self._forget()
        if canvas is None or sip.isdeleted(canvas):
            return
        for signal, slot in ((canvas.destinationCrsChanged, self._reproject), (canvas.destroyed, self._forget)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if item is not None:
            canvas.scene().removeItem(item)

    def _forget(self, *_):
        self.canvas = None
        self.item = None

    def _reproject(self):
        if self.canvas is None or self.item is None:
            return
        point = _to_map_point(self.canvas, self.longitude, self.latitude)
        if point is None:
            self.item.hide()
        else:
            self.item.setCenter(point)
            self.item.show()


class PickerCoordsOnMap(QgsMapTool):
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
        """Remove every coordinate marker the plugin placed.

        Kept on this class: the plugins embedding the widget call it here.
        """
        CoordinateMarker.clear_all()

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.pick(event.mapPoint())

    def pick(self, map_point):
        """Take a point of the canvas, in its CRS, as the coordinate to analyse."""
        widget = self.widget
        try:
            transform = QgsCoordinateTransform(
                self.canvas.mapSettings().destinationCrs(), QgsCoordinateReferenceSystem(WGS84), QgsProject.instance()
            )
            geographic = transform.transform(QgsPointXY(map_point))
        except QgsCsException:
            geographic = None
        if (
            geographic is None
            or not (math.isfinite(geographic.x()) and math.isfinite(geographic.y()))
            or abs(geographic.y()) > 90
        ):
            widget.show_message(
                "That point has no geographic coordinate: it is outside the area the map's coordinate system covers.",
                level=Qgis.MessageLevel.Warning,
            )
            return
        longitude, latitude = normalize_longitude(geographic.x()), geographic.y()
        widget.marker.show(self.canvas, longitude, latitude, map_point=map_point)
        widget.longitude.setValue(longitude)
        widget.latitude.setValue(latitude)

        if widget.auto_generate_plot.isChecked():
            widget.new_plot()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.widget.pick_on_map.setChecked(False)
