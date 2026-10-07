"""Behaviour of the dock in a running QGIS: lifecycle, map tools, markers, failures, embedding."""

import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from typing import ClassVar
from unittest.mock import Mock, patch

RUN_QGIS4_SMOKE = os.environ.get("CCD_RUN_QGIS4_SMOKE") == "1"
SETTLE_TIMEOUT_SECONDS = 30


def process(seconds=0.05):
    """Run the event loop for a while, deferred deletions included."""
    from qgis.PyQt.QtCore import QCoreApplication, QEvent

    deadline = time.monotonic() + seconds
    while True:
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if time.monotonic() > deadline:
            return
        time.sleep(0.005)


def wait_until(test, condition, message, timeout=SETTLE_TIMEOUT_SECONDS):
    from qgis.PyQt.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            test.fail(message)
        QCoreApplication.processEvents()
        time.sleep(0.01)


def run_javascript(test, view, expression):
    result = []
    view.page().runJavaScript(expression, result.append)
    wait_until(test, lambda: result, f"no answer to {expression}", timeout=10)
    return result[0]


@unittest.skipUnless(RUN_QGIS4_SMOKE, "set CCD_RUN_QGIS4_SMOKE=1 inside QGIS 4 to run WebEngine smoke tests")
class QgisTestCase(unittest.TestCase):
    def setUp(self):
        from CCD_Plugin import pre_init_plugin

        pre_init_plugin()
        from qgis.utils import iface

        self.iface = iface
        self.canvas = iface.mapCanvas()
        original_tool = self.canvas.mapTool()
        self.addCleanup(lambda: original_tool is not None and self.canvas.setMapTool(original_tool))
        process()

    def new_canvas(self, crs="EPSG:4326", tool=True):
        from qgis.core import QgsCoordinateReferenceSystem
        from qgis.gui import QgsMapCanvas, QgsMapToolPan

        canvas = QgsMapCanvas()
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem(crs))
        self.addCleanup(lambda: canvas.deleteLater())
        if tool:
            canvas.pan_tool = QgsMapToolPan(canvas)
            canvas.setMapTool(canvas.pan_tool)
        return canvas

    def new_dock(self, canvases, dock_id="lifecycle-test"):
        """A dock as an embedding plugin builds it, registered like they do; over QGIS' own
        canvas, as the plugin builds it, with canvases None."""
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import CCD_PluginDockWidget

        plot_directory = tempfile.mkdtemp(prefix="ccd-smoke-")
        self.addCleanup(shutil.rmtree, plot_directory, True)
        dock = CCD_PluginDockWidget(dock_id, plot_directory, canvas=canvases)
        dock.auto_generate_plot.setChecked(False)
        CCD_Plugin.inst[dock_id] = types.SimpleNamespace(widget=dock, tmp_dir=plot_directory)
        self.addCleanup(CCD_Plugin.inst.pop, dock_id, None)
        self.addCleanup(dock.deleteLater)
        self.addCleanup(dock.release_map_tools)
        self.addCleanup(dock.dispose)
        return dock


class DockLifecycleTest(QgisTestCase):
    def new_plugin(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin

        plugin = CCD_Plugin(self.iface)
        plugin.initGui()
        plugin.run()
        process()
        return plugin

    def test_reopening_reuses_the_dock_and_leaves_nothing_behind(self):
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import CCD_PluginDockWidget, PickerCoordsOnMap

        # Given: the plugin's dock, open.
        plugin = self.new_plugin()
        self.addCleanup(plugin.unload)
        dock = plugin.widget

        for cycle in range(3):
            with self.subTest(cycle=cycle):
                # When: it is used - Advanced dialog open, picking on the map - and closed.
                dock.advanced_settings.show()
                dock.pick_on_map.setChecked(True)
                dock.close()
                process()

                # Then: the dialog, pick mode and the picker go with it,
                self.assertFalse(dock.advanced_settings.isVisible())
                self.assertFalse(dock.pick_on_map.isChecked())
                self.assertNotIsInstance(self.canvas.mapTool(), PickerCoordsOnMap)
                self.assertEqual(self.canvas.findChildren(PickerCoordsOnMap), [])

                # and opening it again shows the same dock, not one more.
                plugin.run()
                process()
                self.assertIs(plugin.widget, dock)
                self.assertTrue(dock.isVisible())
        self.assertEqual(self.iface.mainWindow().findChildren(CCD_PluginDockWidget), [dock])

    def test_unload_deletes_the_dock_its_pickers_and_its_instance(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import PickerCoordsOnMap
        from qgis.PyQt import sip

        # Given: the dock open and picking on the map.
        tool_before = self.canvas.mapTool()
        plugin = self.new_plugin()
        dock = plugin.widget
        dock.pick_on_map.setChecked(True)
        self.assertIsInstance(self.canvas.mapTool(), PickerCoordsOnMap)

        # When: the plugin is unloaded.
        plugin.unload()
        process()

        # Then: the canvas has its tool back and nothing of the plugin survives.
        self.assertIs(self.canvas.mapTool(), tool_before)
        self.assertTrue(sip.isdeleted(dock))
        self.assertEqual(self.canvas.findChildren(PickerCoordsOnMap), [])
        self.assertNotIn(plugin.id, CCD_Plugin.inst)

    def test_a_reopened_dock_completes_its_runs(self):
        from CCD_Plugin.core import ccd_process
        from test_plot_supersession import FakeEarthEngine

        # Given: Earth Engine, faked, and the plugin's dock.
        earth_engine = FakeEarthEngine()
        self.addCleanup(earth_engine.release_all)
        for patcher in (
            patch.dict(sys.modules, {"ee": earth_engine.module}),
            patch.object(ccd_process, "get_gee_data_landsat", earth_engine.collection),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(ccd_process.clear_results_cache)
        plugin = self.new_plugin()
        self.addCleanup(plugin.unload)
        dock = plugin.widget

        def run(longitude):
            dock.longitude.setValue(longitude)
            dock.new_plot()
            earth_engine.release(longitude)
            wait_until(
                self,
                lambda: (
                    dock.task is None and dock.plot_loads.pending is None and dock.plot_files.active_path is not None
                ),
                "the plot never settled",
            )
            self.assertEqual(dock.last_config["lon"], longitude)

        # When: a plot is drawn, the dock closed with it, reopened, and another one drawn.
        run(-70.0)
        dock.close()
        process()
        self.assertIsNone(dock.plot_files.active_path)
        plugin.run()

        # Then: the reopened dock draws it; closing used to disable every later run for good.
        run(-71.0)


class MapToolTest(QgisTestCase):
    def test_on_the_qgis_canvas_picking_hands_back_the_tool_active_when_it_began(self):
        from qgis.gui import QgsMapToolZoom

        # Given: the plugin's own dock, over QGIS' map canvas, and the user switching to zoom
        # after the dock was built.
        dock = self.new_dock(None)
        zoom = QgsMapToolZoom(self.canvas, False)
        self.addCleanup(zoom.deleteLater)
        self.canvas.setMapTool(zoom)

        # When: picking starts and ends.
        dock.pick_on_map.setChecked(True)
        dock.pick_on_map.setChecked(False)

        # Then: zoom is back, not the tool the canvas had when the dock was built.
        self.assertIs(self.canvas.mapTool(), zoom)

    def test_an_embedded_view_gets_its_default_tool_back_not_an_unfinished_editing_tool(self):
        from qgis.gui import QgsMapToolPan
        from qgis.PyQt.QtWidgets import QToolButton

        # Given: a ThRasE view: its default pan tool when the widget is built, then an editing tool
        # switched on through its button.
        canvas = self.new_canvas()
        dock = self.new_dock([canvas])
        editing_tool = QgsMapToolPan(canvas)
        editing_button = QToolButton()
        editing_button.setCheckable(True)
        editing_button.setChecked(True)
        canvas.setMapTool(editing_tool)

        # and ThRasE switching its editing off when picking starts, by its button only, without
        # finishing the tool (main_dialog.coordinates_from_map)
        dock.pick_on_map.toggled.connect(lambda checked: checked and editing_button.setChecked(False))

        # When: a point is picked and picking ends.
        dock.pick_on_map.setChecked(True)
        dock.pick_on_map.setChecked(False)

        # Then: the view's default tool is back. The editing tool was, with its button off, and
        # the next click edited the raster.
        self.assertIs(canvas.mapTool(), canvas.pan_tool)
        self.assertFalse(editing_button.isChecked())

    def test_another_tool_ends_pick_mode_on_every_canvas(self):
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import PickerCoordsOnMap
        from qgis.gui import QgsMapToolZoom

        # Given: a dock picking on two canvases, as AcATaMa and ThRasE embed it.
        first, second = self.new_canvas(), self.new_canvas()
        dock = self.new_dock([first, second])
        dock.pick_on_map.setChecked(True)
        self.assertIsInstance(second.mapTool(), PickerCoordsOnMap)

        # When: the user picks another tool on the first canvas.
        zoom = QgsMapToolZoom(first, False)
        first.setMapTool(zoom)
        process()

        # Then: the button follows, the first canvas keeps the user's tool, the second gets its own back.
        self.assertFalse(dock.pick_on_map.isChecked())
        self.assertIs(first.mapTool(), zoom)
        self.assertIs(second.mapTool(), second.pan_tool)

    def test_an_embedding_plugin_ends_pick_mode_with_set_checked(self):
        # Given: a dock picking on two canvases.
        first, second = self.new_canvas(), self.new_canvas()
        dock = self.new_dock([first, second])
        dock.pick_on_map.setChecked(True)

        # When: ThRasE unchecks the button, as it does when one of its own tools is chosen.
        dock.pick_on_map.setChecked(False)

        # Then: every canvas has its tool back.
        self.assertIs(first.mapTool(), first.pan_tool)
        self.assertIs(second.mapTool(), second.pan_tool)

    def test_a_deleted_previous_tool_is_not_restored(self):
        from qgis.gui import QgsMapToolPan
        from qgis.PyQt import sip

        # Given: picking started over a tool that is deleted before picking ends.
        canvas = self.new_canvas(tool=False)
        tool = QgsMapToolPan(canvas)
        canvas.setMapTool(tool)
        dock = self.new_dock([canvas])
        dock.pick_on_map.setChecked(True)
        sip.delete(tool)

        # When: picking ends - this raised "wrapped C/C++ object has been deleted".
        dock.pick_on_map.setChecked(False)

        # Then: the picker is released instead.
        self.assertIsNone(canvas.mapTool())

    def test_a_canvas_without_a_tool_is_left_without_the_picker(self):
        # Given: a canvas with no tool, which embedded views can have.
        canvas = self.new_canvas(tool=False)
        dock = self.new_dock([canvas])

        # When: picking starts and ends.
        dock.pick_on_map.setChecked(True)
        dock.pick_on_map.setChecked(False)

        # Then: the canvas is back without a tool; setMapTool(None) used to leave the picker active.
        self.assertIsNone(canvas.mapTool())


class MarkerAndPickingTest(QgisTestCase):
    def test_the_marker_follows_a_change_of_the_canvas_crs(self):
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject

        # Given: the coordinate shown on a geographic canvas.
        canvas = self.new_canvas("EPSG:4326")
        dock = self.new_dock([canvas])
        dock.longitude.setValue(-75.0)
        dock.latitude.setValue(5.0)
        dock.show_and_go_to_the_coordinates()

        # When: the canvas switches to Web Mercator.
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))

        # Then: the marker is re-projected instead of staying at (-75, 5) metres.
        expected = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"), canvas.mapSettings().destinationCrs(), QgsProject.instance()
        ).transform(QgsPointXY(-75.0, 5.0))
        center = dock.marker.item.center()
        self.assertAlmostEqual(center.x(), expected.x(), places=3)
        self.assertAlmostEqual(center.y(), expected.y(), places=3)

    def test_delete_markers_on_the_class_clears_every_marker(self):
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import PickerCoordsOnMap

        # Given: two docks, each showing its coordinate - the embedding plugins call this on the class.
        canvas = self.new_canvas()
        docks = [self.new_dock([canvas], f"marker-{index}") for index in range(2)]
        for dock in docks:
            dock.show_and_go_to_the_coordinates()

        PickerCoordsOnMap.delete_markers()

        self.assertEqual([dock.marker.item for dock in docks], [None, None])

    def test_a_marker_lets_go_of_a_destroyed_canvas(self):
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import PickerCoordsOnMap
        from qgis.core import QgsCoordinateReferenceSystem
        from qgis.gui import QgsMapCanvas

        # Given: a marker on a canvas that is then destroyed, as an embedding plugin's view is.
        canvas = QgsMapCanvas()
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        dock = self.new_dock([self.new_canvas()])
        self.assertIsNotNone(dock.marker.show(canvas, -75.0, 5.0))
        canvas.deleteLater()
        process()

        # When/Then: clearing it no longer raises on the deleted canvas.
        PickerCoordsOnMap.delete_markers()
        self.assertIsNone(dock.marker.canvas)

    def test_a_click_past_the_antimeridian_is_wrapped(self):
        from qgis.core import QgsPointXY

        # Given: a geographic canvas panned east of the dateline.
        canvas = self.new_canvas("EPSG:4326")
        dock = self.new_dock([canvas])

        # When: a point at longitude 190 is picked.
        dock.picker_for(canvas).pick(QgsPointXY(190.0, 10.0))

        # Then: the analysed longitude is -170, not clamped to 180, and the marker stays where clicked.
        self.assertAlmostEqual(dock.longitude.value(), -170.0)
        self.assertAlmostEqual(dock.latitude.value(), 10.0)
        self.assertAlmostEqual(dock.marker.item.center().x(), 190.0)

    def test_a_picked_coordinate_keeps_centimetre_precision(self):
        from qgis.core import QgsPointXY

        canvas = self.new_canvas("EPSG:4326")
        dock = self.new_dock([canvas])

        dock.picker_for(canvas).pick(QgsPointXY(-75.123456789, 5.987654321))

        self.assertAlmostEqual(dock.longitude.value(), -75.1234568, places=7)
        self.assertAlmostEqual(dock.latitude.value(), 5.9876543, places=7)

    def test_a_point_without_a_geographic_coordinate_is_refused(self):
        from qgis.core import QgsPointXY

        # Given: a point beyond the pole of a geographic canvas.
        canvas = self.new_canvas("EPSG:4326")
        dock = self.new_dock([canvas])
        dock.latitude.setValue(5.0)

        # When: it is picked.
        dock.picker_for(canvas).pick(QgsPointXY(0.0, 95.0))

        # Then: the user is told, and the coordinate is not clamped to the pole.
        self.assertEqual(dock.latitude.value(), 5.0)
        self.assertEqual(len(dock.MsgBar.items()), 1)


class FailureRecoveryTest(QgisTestCase):
    REGION: ClassVar[dict] = {"time": [0.0, 86_400_000.0], "SWIR1": [0.1, 0.2]}

    def showing(self, dock, path):
        from qgis.PyQt.QtCore import QUrl

        return dock.plot_webview.url() == QUrl.fromLocalFile(str(path))

    def start_fake_task(self, dock):
        from CCD_Plugin.core.loading import loading_page_html
        from CCD_Plugin.utils.config import get_plugin_config

        config = get_plugin_config(dock.id)
        task = types.SimpleNamespace(cancel=lambda: None, isCanceled=lambda: False)
        dock.task = task
        dock.task_lifecycle.start(task)
        dock.pending_config = config
        dock.plot_webview.setHtml(loading_page_html(dock.plot_style))
        process(0.3)
        return task, config

    def showing_spinner(self, dock):
        process(0.3)
        return run_javascript(self, dock.plot_webview, "Boolean(document.querySelector('.spinner'))")

    def crash(self, dock):
        """The view's renderer dying, as QWebEnginePage reports it."""
        from qgis.PyQt.QtWebEngineCore import QWebEnginePage

        dock._render_process_terminated(QWebEnginePage.RenderProcessTerminationStatus.CrashedTerminationStatus, 1)

    def plot_on_display(self, dock):
        """Put a plot on display, its configuration in self.config, and return its file."""
        _task, self.config = self.start_fake_task(dock)
        dock.task_lifecycle.cancel()
        dock.task = None
        return self.commit_plot(dock, self.config)

    def cache_result(self, config):
        """Put a result for `config` in the CCD cache, built without any index."""
        from CCD_Plugin.core import ccd_process
        from CCD_Plugin.core.ccd_process import _store_result, make_cache_key

        self.addCleanup(ccd_process.clear_results_cache)
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
        _store_result(key, (), ({}, self.REGION))

    def commit_plot(self, dock, config):
        dock.load_plot({}, self.REGION, config)
        wait_until(self, lambda: dock.plot_loads.pending is None, "the plot never loaded")
        self.assertIsNotNone(dock.plot_files.active_path)
        return dock.plot_files.active_path

    def test_a_failed_run_without_a_plot_leaves_an_empty_view(self):
        # Given: a run in progress, the loading page up, and no plot drawn before.
        dock = self.new_dock([self.new_canvas()])
        task, _config = self.start_fake_task(dock)

        # When: the run fails.
        dock.ccd_completed(task, RuntimeError("Earth Engine said no"))

        # Then: the failure is reported and the loading page is gone.
        self.assertFalse(self.showing_spinner(dock))
        self.assertIn("Earth Engine said no", dock.MsgBar.items()[0].text())
        self.assertTrue(dock.generate_button.isEnabled())
        self.assertFalse(dock.plot_in_progress())

    def test_a_plot_that_cannot_be_written_is_reported_and_the_previous_one_restored(self):
        from CCD_Plugin.gui import CCD_Plugin_dockwidget as dock_module

        # Given: a plot on display and a newer run in progress.
        dock = self.new_dock([self.new_canvas()])
        _task, config = self.start_fake_task(dock)
        dock.task_lifecycle.cancel()
        dock.task = None
        active = self.commit_plot(dock, config)
        task, config = self.start_fake_task(dock)

        # When: writing its plot fails, inside the completion callback QGIS swallows errors from.
        with patch.object(dock_module, "generate_plot", side_effect=OSError("disk full")):
            dock.ccd_completed(task, None, (config, {}, self.REGION))

        # Then: it is reported, and the previous plot is back instead of the loading page.
        self.assertIn("disk full", dock.MsgBar.items()[0].text())
        wait_until(self, lambda: self.showing(dock, active), "the plot was not restored")
        self.assertFalse(self.showing_spinner(dock))
        self.assertTrue(dock.generate_button.isEnabled())

    def test_a_stopped_load_releases_the_pending_plot(self):
        from qgis.PyQt.QtWebEngineCore import QWebEnginePage

        # Given: a plot loading into the view.
        dock = self.new_dock([self.new_canvas()])
        _task, config = self.start_fake_task(dock)
        dock.task_lifecycle.cancel()
        dock.task = None
        dock.load_plot({}, self.REGION, config)

        # When: the load is stopped once it has started, before it finishes.
        process(0.02)
        dock.plot_webview.page().triggerAction(QWebEnginePage.WebAction.Stop)

        # Then: nothing stays pending, so Generate can start that plot again.
        wait_until(self, lambda: dock.plot_loads.pending is None, "the stopped load stayed pending")
        self.assertIsNone(dock.plot_files.pending_path)
        self.assertFalse(dock.plot_in_progress())

    def test_a_crashed_view_shows_the_plot_again(self):
        from qgis.PyQt.QtWebEngineCore import QWebEnginePage

        # Given: a plot on display.
        dock = self.new_dock([self.new_canvas()])
        _task, config = self.start_fake_task(dock)
        dock.task_lifecycle.cancel()
        dock.task = None
        active = self.commit_plot(dock, config)
        dock.plot_webview.setHtml("<html><body>gone</body></html>")
        process(0.3)

        # When: its renderer dies.
        crashed = QWebEnginePage.RenderProcessTerminationStatus.CrashedTerminationStatus
        dock._render_process_terminated(crashed, 1)

        # Then: the plot is loaded again,
        wait_until(self, lambda: self.showing(dock, active), "the plot was not reloaded")

        # but when it dies again on that same plot, it is not reloaded in a loop.
        dock._render_process_terminated(crashed, 1)
        process(0.3)
        self.assertFalse(self.showing(dock, active))
        self.assertIn("keeps stopping", dock.MsgBar.items()[0].text())

    def test_a_crash_long_after_the_reload_reloads_the_plot_again(self):
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import CRASH_REPEAT_SECONDS, CrashReload

        # Given: a plot reloaded after a crash, and on display without trouble since.
        dock = self.new_dock([self.new_canvas()])
        active = self.plot_on_display(dock)
        self.crash(dock)
        wait_until(self, lambda: self.showing(dock, active), "the plot was not reloaded")
        dock.crash_reload = CrashReload(active, dock.crash_reload.at - CRASH_REPEAT_SECONDS - 1)

        # When: the view crashes again, for an unrelated reason.
        self.crash(dock)

        # Then: the plot is reloaded once more rather than taken off the view.
        wait_until(self, lambda: self.showing(dock, active), "the plot was not reloaded")
        self.assertNotIn("keeps stopping", dock.MsgBar.items()[0].text())

    def test_a_crash_while_a_newer_plot_loads_puts_the_plot_on_display_back(self):
        # Given: a plot that crashed the view once and was reloaded, and a newer plot loading.
        dock = self.new_dock([self.new_canvas()])
        active = self.plot_on_display(dock)
        self.crash(dock)
        wait_until(self, lambda: self.showing(dock, active), "the plot was not reloaded")
        dock.load_plot({}, self.REGION, self.config)

        # When: the view crashes while the newer plot loads.
        self.crash(dock)

        # Then: the newer plot is dropped and the one on display is back, not blamed and blanked.
        wait_until(self, lambda: self.showing(dock, active), "the plot on display was not put back")
        self.assertIsNone(dock.plot_files.pending_path)
        self.assertFalse(dock.plot_in_progress())
        self.assertIn("previous one is shown again", dock.MsgBar.items()[0].text())

    def test_a_crash_while_the_first_plot_loads_leaves_an_empty_view(self):
        # Given: the first plot of the dock loading, with nothing on display before it.
        dock = self.new_dock([self.new_canvas()])
        _task, config = self.start_fake_task(dock)
        dock.task_lifecycle.cancel()
        dock.task = None
        dock.load_plot({}, self.REGION, config)

        # When: the view crashes while it loads.
        self.crash(dock)

        # Then: the view is empty, not the loading page, and no previous plot is promised.
        self.assertFalse(self.showing_spinner(dock))
        self.assertFalse(dock.plot_in_progress())
        message = dock.MsgBar.items()[0].text()
        self.assertIn("press Generate to try again", message)
        self.assertNotIn("previous one", message)

    def test_a_band_switch_after_the_crash_guard_recomputes_rather_than_refusing(self):
        # Given: a plot taken off the view by the crash guard, its result cached without NDVI.
        dock = self.new_dock([self.new_canvas()])
        self.plot_on_display(dock)
        self.cache_result(self.config)
        self.crash(dock)
        self.crash(dock)
        dock.start_ccd_task = Mock()

        # When: the band is switched to NDVI, which the cached result cannot serve.
        dock.band_or_index_to_plot.setCurrentText("NDVI")

        # Then: it is computed, as for the plot on display; nothing about the settings changed.
        dock.start_ccd_task.assert_called_once()
        self.assertNotIn("settings changed", " ".join(item.text() for item in dock.MsgBar.items()))

    def test_generate_draws_again_a_plot_the_crash_guard_took_down(self):
        # Given: a plot on display, its result in the cache, and its view crashing twice on it.
        dock = self.new_dock([self.new_canvas()])
        first = self.plot_on_display(dock)
        config = self.config
        self.cache_result(config)
        self.crash(dock)
        self.crash(dock)

        # When: Generate is pressed with the same settings.
        with patch.dict(sys.modules, {"ee": types.SimpleNamespace()}):
            dock.new_plot()

        # Then: the plot is drawn again from the cache, instead of being refused as already drawn.
        wait_until(
            self,
            lambda: (
                dock.plot_files.active_path not in (None, first) and self.showing(dock, dock.plot_files.active_path)
            ),
            "the plot was not drawn again",
        )
        self.assertNotIn("nothing to recompute", " ".join(item.text() for item in dock.MsgBar.items()))


class EmbeddingTest(QgisTestCase):
    """The widget as AcATaMa and ThRasE use it: built inside their dialog, over their view canvases."""

    def test_an_embedded_widget_keeps_working_the_way_those_plugins_drive_it(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import CCD_PluginDockWidget, PickerCoordsOnMap
        from CCD_Plugin.utils.config import get_plugin_config, restore_plugin_config
        from qgis.gui import QgsMapToolPan
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QDialog, QDockWidget, QWidget

        # Given: the widget built the way they build it.
        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        first, second = self.new_canvas(), self.new_canvas()
        plugin = CCD_Plugin(self.iface)
        self.addCleanup(CCD_Plugin.inst.pop, plugin.id, None)
        plugin.widget = CCD_PluginDockWidget(id=plugin.id, canvas=[first, second], parent=dialog)
        widget = plugin.widget
        widget.setWindowFlags(Qt.WindowType.Widget)
        widget.setTitleBarWidget(QWidget(None))
        widget.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        widget.MainWidget.layout().setSpacing(3)
        plugin.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, plugin.tmp_dir, True)

        # When: a configuration saved in one of their projects by an older version is restored.
        legacy = {"lat": 4.5, "lon": -74.1, "dataset": "Landsat col. 2", "lambda_lasso": 20, "num_obs": 5}
        restore_plugin_config(plugin.id, legacy)

        # Then: the project still opens with what is valid, and the configuration keeps its keys.
        self.assertEqual((widget.latitude.value(), widget.dataset.currentText()), (4.5, "Landsat C2"))
        self.assertEqual(widget.advanced_settings.num_obs.value(), 5)
        self.assertEqual(
            list(get_plugin_config(plugin.id)),
            [
                "lat",
                "lon",
                "dataset",
                "band_or_index_to_plot",
                "plot_style",
                "breakpoint_bands",
                "start_date",
                "end_date",
                "start_doy",
                "end_doy",
                "num_obs",
                "chi_square",
                "min_years",
                "lambda_lasso",
                "cloud_filter",
                "auto_generate_plot",
            ],
        )

        # When: picking is started with click(), and ThRasE takes the first view for its own tool.
        widget.pick_on_map.click()
        self.assertIsInstance(second.mapTool(), PickerCoordsOnMap)
        thrase_tool = QgsMapToolPan(first)
        first.setMapTool(thrase_tool)
        widget.pick_on_map.setChecked(False)
        process()

        # Then: ThRasE keeps its tool and the other view gets its own back.
        self.assertIs(first.mapTool(), thrase_tool)
        self.assertIs(second.mapTool(), second.pan_tool)

        # And: their teardown calls still work, and release() forgets the instance but leaves the
        # temporary directory they handed in - ThRasE shares its own with the rest of its files.
        PickerCoordsOnMap.delete_markers()
        widget.close()
        tmp_dir = plugin.tmp_dir
        plugin.release()
        self.assertNotIn(plugin.id, CCD_Plugin.inst)
        self.assertTrue(os.path.isdir(tmp_dir))

    def test_release_removes_the_temporary_directory_it_created(self):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from CCD_Plugin.gui.CCD_Plugin_dockwidget import CCD_PluginDockWidget
        from CCD_Plugin.utils.config import get_plugin_tmp_dir
        from qgis.PyQt.QtWidgets import QDialog

        # Given: an embedded widget left to create its own temporary directory.
        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        plugin = CCD_Plugin(self.iface)
        self.addCleanup(CCD_Plugin.inst.pop, plugin.id, None)
        plugin.widget = CCD_PluginDockWidget(id=plugin.id, canvas=[self.new_canvas()], parent=dialog)
        created = get_plugin_tmp_dir(plugin.id)

        # and the embedding plugin handing in a directory of its own afterwards.
        handed_in = tempfile.mkdtemp(prefix="ccd-embedder-")
        self.addCleanup(shutil.rmtree, handed_in, True)
        plugin.tmp_dir = handed_in

        # When/Then: release() removes the one it created, as nothing else would, and only that one.
        plugin.release()
        self.assertFalse(os.path.isdir(created))
        self.assertTrue(os.path.isdir(handed_in))


if __name__ == "__main__":
    unittest.main()
