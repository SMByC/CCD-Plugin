import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

RUN_QGIS4_SMOKE = os.environ.get("CCD_RUN_QGIS4_SMOKE") == "1"

DOCK_ID = "plot-supersession-test"
BANDS = [
    *("Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2"),
    *("NDVI", "NBR", "EVI", "EVI2", "BRIGHTNESS", "GREENNESS", "WETNESS"),
]
REGION_HEADER = ["id", "longitude", "latitude", "time", *BANDS]
CATALOG = {"size": 2, "projection": {"crs": "EPSG:4326", "transform": [1, 0, 0, 0, 1, 0]}}
SETTLE_TIMEOUT_SECONDS = 30
# well under the time a silent Earth Engine request blocks for, so a superseded run that waits for
# its requests instead of giving up its task fails the test rather than slowing it down
PROMPT_CANCEL_TIMEOUT_SECONDS = 5
# Far beyond every wait above. A request that times out fails its run, and a failed run puts the
# previous plot back, which can make a superseded run look correctly dropped when it never was.
# Cleanup releases every request, so this only bounds a run nobody released.
SILENT_REQUEST_TIMEOUT_SECONDS = 4 * SETTLE_TIMEOUT_SECONDS


class FakeEarthEngine:
    """Just enough of ee for compute_ccd, answering for a point only once the test releases it.

    getInfo blocks and never looks at cancellation, like the real client.
    """

    def __init__(self):
        self._gates = {}
        self._lock = threading.Lock()
        self.module = types.SimpleNamespace(
            Initialize=lambda *_, **__: None,
            Geometry=types.SimpleNamespace(Point=lambda coords: coords),
            Dictionary=lambda request: self._request(request["size"], CATALOG),
            Image=lambda _: types.SimpleNamespace(select=lambda _: types.SimpleNamespace(projection=lambda: None)),
            Projection=lambda _: None,
            List=lambda longitude: self._request(longitude, self._region(longitude)),
            Reducer=types.SimpleNamespace(toList=lambda: None),
            Algorithms=types.SimpleNamespace(
                If=lambda *_: None,
                TemporalSegmentation=types.SimpleNamespace(
                    Ccdc=lambda collection, *_: types.SimpleNamespace(
                        reduceRegion=lambda *_, **__: self._request(collection.size(), {})
                    )
                ),
            ),
        )

    def collection(self, coords, *_):
        """Stands in for get_gee_data_landsat; every request it leads to carries the longitude."""
        longitude = coords[0]
        return types.SimpleNamespace(first=lambda: None, size=lambda: longitude, getRegion=lambda **_: longitude)

    def release(self, longitude):
        self._gate(longitude).set()

    def release_all(self):
        with self._lock:
            gates = list(self._gates.values())
        for gate in gates:
            gate.set()

    def _gate(self, longitude):
        with self._lock:
            return self._gates.setdefault(longitude, threading.Event())

    def _request(self, longitude, answer):
        gate = self._gate(longitude)

        def get_info():
            if not gate.wait(timeout=SILENT_REQUEST_TIMEOUT_SECONDS):
                raise TimeoutError(f"Earth Engine was never released for {longitude}")
            return answer

        return types.SimpleNamespace(getInfo=get_info)

    @staticmethod
    def _region(longitude):
        return [
            REGION_HEADER,
            ["a", longitude, 0, 0.0, *[0.1] * len(BANDS)],
            ["b", longitude, 0, 86_400_000.0, *[0.2] * len(BANDS)],
        ]


@unittest.skipUnless(RUN_QGIS4_SMOKE, "set CCD_RUN_QGIS4_SMOKE=1 inside QGIS 4 to run WebEngine smoke tests")
class PlotSupersessionSmokeTest(unittest.TestCase):
    """However launches overlap, the plot left on display is the one the latest launch asked for."""

    def setUp(self):
        # the bundled plotly, put on the path the way classFactory does when QGIS loads the plugin
        from CCD_Plugin import pre_init_plugin

        pre_init_plugin()

        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        from CCD_Plugin.core import ccd_process
        from CCD_Plugin.gui import CCD_Plugin_dockwidget as dock_module
        from qgis.core import QgsApplication
        from qgis.gui import QgsMapCanvas

        self.task_manager = QgsApplication.taskManager()
        self.earth_engine = FakeEarthEngine()
        ccd_process.clear_results_cache()
        self.addCleanup(ccd_process.clear_results_cache)

        # what each plot file was drawn for, read back for the file left on display
        self.plotted = {}
        generate_plot = dock_module.generate_plot

        def recording_generate_plot(ccdc_result_info, timeseries, spec, files, **kwargs):
            path = generate_plot(ccdc_result_info, timeseries, spec, files, **kwargs)
            self.plotted[path] = spec
            return path

        for patcher in (
            patch.dict(sys.modules, {"ee": self.earth_engine.module}),
            patch.object(ccd_process, "get_gee_data_landsat", self.earth_engine.collection),
            patch.object(dock_module, "generate_plot", recording_generate_plot),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        plot_directory = tempfile.TemporaryDirectory()
        self.addCleanup(plot_directory.cleanup)
        self.canvas = QgsMapCanvas()
        self.dock = dock_module.CCD_PluginDockWidget(DOCK_ID, plot_directory.name, canvas=[self.canvas])
        CCD_Plugin.inst[DOCK_ID] = types.SimpleNamespace(widget=self.dock, tmp_dir=plot_directory.name)
        self.addCleanup(CCD_Plugin.inst.pop, DOCK_ID, None)
        self.addCleanup(self.dock.deleteLater)
        # cleanups run last-in first-out: dispose, then drain while Earth Engine is still faked
        self.addCleanup(self.drain)
        self.addCleanup(self.dock.dispose)
        self.dock.start_ccd_task = Mock(wraps=self.dock.start_ccd_task)

    def drain(self):
        """Let every request still blocked finish, so no task outlives the test."""
        self.earth_engine.release_all()
        self.wait_until(lambda: self.task_manager.countActiveTasks() == 0, "a CCD task never finished")

    def wait_until(self, condition, message, timeout=SETTLE_TIMEOUT_SECONDS):
        from qgis.PyQt.QtCore import QCoreApplication

        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail(message)
            QCoreApplication.processEvents()
            time.sleep(0.01)

    def settle(self):
        self.wait_until(
            lambda: self.dock.task is None and self.dock.plot_loads.pending is None, "the plot never settled"
        )

    def launch(self, longitude):
        self.dock.longitude.setValue(longitude)
        self.dock.new_plot()

    def plot(self, longitude):
        self.launch(longitude)
        self.earth_engine.release(longitude)
        self.settle()

    def assertShowing(self, longitude, band="SWIR1"):
        active = self.dock.plot_files.active_path
        spec = self.plotted.get(str(active)) if active is not None else None
        self.assertIsNotNone(spec, "no plot on display")
        self.assertEqual((spec.longitude, spec.band), (longitude, band))
        self.assertEqual(self.dock.last_config["lon"], longitude)
        self.assertTrue(self.dock.generate_button.isEnabled())
        self.assertTrue(self.dock.band_or_index_to_plot.isEnabled())

    def test_superseded_run_never_reaches_the_view_and_gives_up_its_task(self):
        # Given: a run for one point that Earth Engine has not answered.
        self.launch(-70.0)

        # When: another point is launched and answered.
        self.launch(-71.0)
        self.earth_engine.release(-71.0)
        self.settle()

        # Then: that point is on display, and the superseded run has already released its task,
        # although Earth Engine still has not answered it.
        self.assertShowing(-71.0)
        self.wait_until(
            lambda: self.task_manager.countActiveTasks() == 0,
            "the superseded run kept its task until Earth Engine answered",
            timeout=PROMPT_CANCEL_TIMEOUT_SECONDS,
        )

    def test_returning_to_the_plot_on_display_drops_the_newer_run(self):
        # Given: one point on display and a newer one still computing.
        self.plot(-70.0)
        self.launch(-71.0)
        self.dock.start_ccd_task.reset_mock()

        # When: the point on display is launched again, and only then the newer run is answered.
        self.launch(-70.0)
        self.earth_engine.release(-71.0)
        self.drain()
        self.settle()

        # Then: that point stays on display, redrawn from the cache instead of recomputed.
        self.assertShowing(-70.0)
        self.dock.start_ccd_task.assert_not_called()

    def test_band_switched_mid_run_is_the_band_plotted(self):
        # Given: one point on display and a newer one still computing.
        self.plot(-70.0)
        self.launch(-71.0)

        # When: the band changes before the run is answered, as a configuration restore does.
        self.dock.band_or_index_to_plot.setCurrentText("NIR")
        self.earth_engine.release(-71.0)
        self.settle()

        # Then: the run lands with the band now selected, not the one it started with.
        self.assertShowing(-71.0, "NIR")

    def test_cached_redraw_mid_run_is_not_replaced_by_that_run(self):
        # Given: one point on display and a newer one still computing.
        self.plot(-70.0)
        self.launch(-71.0)

        # When: a restore brings back the point on display with another band, served from the
        # cache, and only then the newer run is answered.
        self.dock.longitude.setValue(-70.0)
        self.dock.band_or_index_to_plot.setCurrentText("NIR")
        self.earth_engine.release(-71.0)
        self.drain()
        self.settle()

        # Then: the redraw stays, and the run it superseded does not land over it.
        self.assertShowing(-70.0, "NIR")

    def test_switching_to_a_band_the_run_builds_keeps_the_run(self):
        # Given: a run in progress, plotting SWIR1; it builds every optical band.
        self.launch(-71.0)
        self.dock.start_ccd_task.reset_mock()

        # When: the band switches to another optical band, as a configuration restore does.
        self.dock.band_or_index_to_plot.setCurrentText("NIR")
        self.earth_engine.release(-71.0)
        self.settle()

        # Then: the run was kept, not restarted from scratch, and lands with that band.
        self.assertShowing(-71.0, "NIR")
        self.dock.start_ccd_task.assert_not_called()

    def test_switching_to_an_index_the_run_does_not_build_restarts_it(self):
        # Given: a run in progress, plotting SWIR1, which builds no index.
        self.launch(-71.0)
        self.dock.start_ccd_task.reset_mock()

        # When: the band switches to NDVI.
        self.dock.band_or_index_to_plot.setCurrentText("NDVI")
        self.earth_engine.release(-71.0)
        self.settle()

        # Then: the run is restarted so the index is built, and the plot shows it.
        self.assertShowing(-71.0, "NDVI")
        self.dock.start_ccd_task.assert_called_once()

    def test_launching_the_run_in_progress_again_keeps_it(self):
        # Given: a run that Earth Engine has not answered.
        self.launch(-71.0)

        # When: the same point is launched again, as an embedding plugin re-selecting it does.
        self.launch(-71.0)
        self.earth_engine.release(-71.0)
        self.settle()

        # Then: the run in progress was kept rather than restarted.
        self.assertShowing(-71.0)
        self.dock.start_ccd_task.assert_called_once()


if __name__ == "__main__":
    unittest.main()
