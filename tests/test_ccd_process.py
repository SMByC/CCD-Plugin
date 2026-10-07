import concurrent.futures
import sys
import threading
import time
import types
import unittest
from collections import OrderedDict
from unittest.mock import Mock, patch

import core.ccd_process as ccd_process_module
from core.ccd_process import (
    DATASET_AVAILABILITY,
    DEFAULT_BREAKPOINT_BANDS,
    REQUEST_THREAD_NAME,
    CCDComputationError,
    _no_images_message,
    _store_result,
    ccd_results,
    clear_results_cache,
    compute_ccd,
    correlated_detection_bands,
    ensure_earth_engine_initialized,
    lookup_result,
    resolve_computed_indices,
)

REGION_HEADER = ["id", "longitude", "latitude", "time", "Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2"]
REGION_ROWS = [REGION_HEADER, ["a", 0, 0, 0.0, *[0.1] * 6], ["b", 0, 0, 86_400_000.0, *[0.2] * 6]]
CATALOG = {"size": 2, "projection": {"crs": "EPSG:4326", "transform": [1, 0, 0, 0, 1, 0]}}


class _Request:
    def __init__(self, answer):
        self._answer = answer

    def getInfo(self):
        return self._answer()


def _fake_earth_engine(catalog=lambda: CATALOG, region=lambda: REGION_ROWS, ccdc=dict):
    """Just enough of ee for compute_ccd; each callable answers one kind of getInfo.

    Returns the ee module and the collection get_gee_data_landsat should hand back.
    """
    collection = types.SimpleNamespace(first=lambda: None, size=lambda: None, getRegion=lambda **_: None)
    image = types.SimpleNamespace(select=lambda _: types.SimpleNamespace(projection=lambda: None))
    fake_ee = types.SimpleNamespace(
        Geometry=types.SimpleNamespace(Point=lambda coords: coords),
        Dictionary=lambda _: _Request(catalog),
        Image=lambda _: image,
        Projection=lambda _: None,
        List=lambda _: _Request(region),
        Reducer=types.SimpleNamespace(toList=lambda: None),
        Algorithms=types.SimpleNamespace(
            If=lambda *_: None,
            TemporalSegmentation=types.SimpleNamespace(
                Ccdc=lambda *_: types.SimpleNamespace(reduceRegion=lambda *_, **__: _Request(ccdc))
            ),
        ),
    )
    return fake_ee, collection


class NoImagesMessageTest(unittest.TestCase):
    def test_range_entirely_before_the_dataset_says_so(self):
        # Given: a Sentinel-2 range that ends before Sentinel-2 existed. The plugin's date range
        # starts in 2000 by default, so this is easy to land on by narrowing the end date.
        message = _no_images_message("Sentinel-2", ("2000-01-01", "2016-01-01"))

        # Then: the message names the real reason rather than suggesting a wider range.
        self.assertIn("2017-03-28", message)
        self.assertIn("Landsat C2", message)
        self.assertNotIn("wider", message)

    def test_range_inside_the_dataset_keeps_the_generic_advice(self):
        # Given: a range Sentinel-2 does cover, so emptiness is about the point or the DOY window.
        message = _no_images_message("Sentinel-2", ("2020-01-01", "2024-01-01"))

        # Then: it points at the point/range rather than at the dataset's start.
        self.assertNotIn("2017-03-28", message)
        self.assertIn("date and DOY range", message)

    def test_every_supported_dataset_has_an_availability_note(self):
        # Given: the datasets compute_ccd accepts.
        # Then: each can explain itself when it returns nothing.
        for dataset in ("Landsat C2", "Sentinel-2"):
            with self.subTest(dataset=dataset):
                self.assertIn(dataset, DATASET_AVAILABILITY)

    def test_unknown_dataset_still_produces_a_message(self):
        # Given: a dataset with no availability entry.
        # Then: the generic message is returned rather than raising.
        self.assertIn("No images at this point", _no_images_message("Something else", ("2020-01-01", "2024-01-01")))

    def test_a_range_ending_on_the_first_day_of_the_dataset_covers_it(self):
        # Given: a range ending on the day Sentinel-2 starts; the end date is included.
        message = _no_images_message("Sentinel-2", ("2000-01-01", DATASET_AVAILABILITY["Sentinel-2"][0]))

        # Then: that day may have images, so the dataset's start is not blamed.
        self.assertNotIn("has no data before", message)


class CorrelatedDetectionBandsTest(unittest.TestCase):
    def test_the_default_bands_repeat_nothing(self):
        self.assertEqual(correlated_detection_bands(DEFAULT_BREAKPOINT_BANDS), ())

    def test_an_index_next_to_its_own_bands_is_flagged(self):
        # Given: NDVI added to the default set, which already holds its Red and NIR.
        # Then: it is reported as counting the same deviation twice.
        self.assertEqual(correlated_detection_bands([*DEFAULT_BREAKPOINT_BANDS, "NDVI"]), ("NDVI",))

    def test_an_index_whose_bands_are_absent_is_not_flagged(self):
        # Given: NDVI alone; the TMask bands added to it (Green, SWIR1) are not its sources.
        self.assertEqual(correlated_detection_bands(["NDVI"]), ())

    def test_indices_sharing_bands_with_each_other_are_both_flagged(self):
        self.assertEqual(correlated_detection_bands(["NDVI", "EVI2"]), ("NDVI", "EVI2"))

    def test_the_tasseled_cap_overlaps_the_tmask_bands_it_is_computed_from(self):
        # Given: brightness alone; Green and SWIR1 join it as TMask bands, and it is a weighted sum
        # over every optical band, them included.
        self.assertEqual(correlated_detection_bands(["BRIGHTNESS"]), ("BRIGHTNESS",))


class EarthEngineInitializationTest(unittest.TestCase):
    ALGORITHMS = types.SimpleNamespace(TemporalSegmentation=types.SimpleNamespace(Ccdc=None))

    def fake_ee(self, data, algorithms=ALGORITHMS, initialize=None):
        return types.SimpleNamespace(Initialize=initialize or Mock(), Reset=Mock(), data=data, Algorithms=algorithms)

    def initialize(self, fake_ee):
        with patch.dict(sys.modules, {"ee": fake_ee}):
            ensure_earth_engine_initialized()
        return fake_ee.Initialize

    def test_an_initialized_client_is_left_untouched(self):
        # Given: a client the Earth Engine plugin already initialized with the user's project.
        # Then: it is not initialized again, which cost network round trips and reset its state.
        self.initialize(self.fake_ee(types.SimpleNamespace(is_initialized=lambda: True))).assert_not_called()

    def test_an_uninitialized_client_is_initialized(self):
        self.initialize(self.fake_ee(types.SimpleNamespace(is_initialized=lambda: False))).assert_called_once_with()

    def test_older_clients_are_read_through_their_private_flag(self):
        self.initialize(self.fake_ee(types.SimpleNamespace(_initialized=True))).assert_not_called()
        self.initialize(self.fake_ee(types.SimpleNamespace(_initialized=False))).assert_called_once_with()

    def test_a_client_flagged_initialized_without_its_algorithms_is_initialized_again(self):
        # Given: a first initialization that failed after flagging the client initialized, before
        # the algorithm catalogue was loaded - offline on the first Generate.
        half_initialized = self.fake_ee(types.SimpleNamespace(is_initialized=lambda: True), algorithms=object())

        # Then: it is initialized again rather than every later run failing for the session.
        self.initialize(half_initialized).assert_called_once_with()

    def test_a_failed_initialization_is_reset_and_reported(self):
        # Given: an initialization that fails.
        failing = self.fake_ee(
            types.SimpleNamespace(is_initialized=lambda: False), initialize=Mock(side_effect=OSError("offline"))
        )

        # When/Then: the error reaches the run, and the half-initialized state is dropped.
        with self.assertRaisesRegex(OSError, "offline"):
            self.initialize(failing)
        failing.Reset.assert_called_once_with()


class ComputedIndicesTest(unittest.TestCase):
    def test_default_configuration_needs_no_indices(self):
        # Given: change detection on the optical bands with an optical band plotted.
        # Then: not one spectral index has to be built.
        self.assertEqual(resolve_computed_indices(["Green", "Red", "NIR", "SWIR1", "SWIR2"], "SWIR1"), ())

    def test_plotted_index_is_built_even_when_detection_does_not_use_it(self):
        # Given: optical change detection but NDVI on screen.
        # Then: NDVI is built so the series and its coefficients exist.
        self.assertEqual(resolve_computed_indices(["Green", "Red", "NIR", "SWIR1", "SWIR2"], "NDVI"), ("NDVI",))

    def test_breakpoint_indices_are_built_and_tmask_bands_add_none(self):
        # Given: change detection on an index; the TMask bands unioned in are both optical.
        # Then: only that index is built.
        self.assertEqual(resolve_computed_indices(["NBR"], "SWIR1"), ("NBR",))


class CacheLookupTest(unittest.TestCase):
    def setUp(self):
        clear_results_cache()
        self.addCleanup(clear_results_cache)

    def test_a_run_serves_any_view_needing_a_subset_of_its_indices(self):
        # Given: a run that built NDVI.
        _store_result("k", ("NDVI",), ("fit", "series"))

        # Then: it answers a view needing NDVI, and one needing no index at all - switching to an
        # optical band must never force a recompute.
        self.assertEqual(lookup_result("k", ("NDVI",)), ("fit", "series"))
        self.assertEqual(lookup_result("k", ()), ("fit", "series"))

    def test_a_run_cannot_serve_a_view_needing_an_index_it_did_not_build(self):
        # Given: a run that built nothing but the optical bands.
        _store_result("k", (), ("fit", "series"))

        # Then: a view needing NBR is a miss, because that column does not exist.
        self.assertIsNone(lookup_result("k", ("NBR",)))

    def test_a_narrower_run_never_replaces_a_wider_one(self):
        # Given: a run that built two indices, then a narrower run for the same key.
        _store_result("k", ("NDVI", "NBR"), ("wide", "series"))
        _store_result("k", ("NDVI",), ("narrow", "series"))

        # Then: the wider result is kept, so the NBR view still hits.
        self.assertEqual(lookup_result("k", ("NBR",)), ("wide", "series"))

    def test_a_wider_run_replaces_a_narrower_one(self):
        # Given: a narrow run followed by a wider one for the same key.
        _store_result("k", (), ("narrow", "series"))
        _store_result("k", ("NDVI",), ("wide", "series"))

        # Then: the wider result wins and serves both views.
        self.assertEqual(lookup_result("k", ("NDVI",)), ("wide", "series"))
        self.assertEqual(lookup_result("k", ()), ("wide", "series"))

    def test_missing_key_is_a_miss(self):
        self.assertIsNone(lookup_result("nothing here", ()))

    def test_cancelled_result_is_not_stored(self):
        # Given: unload cancellation has been observed before cache publication.
        # When: a completed Earth Engine result reaches the cache boundary.
        stored = _store_result("k", (), ("fit", "series"), cancelled=lambda: True)

        # Then: the cancelled run cannot repopulate the cleared cache.
        self.assertFalse(stored)
        self.assertNotIn("k", ccd_results)

    def test_clear_is_atomic_with_cancellation_check_and_store(self):
        # Given: cache publication is paused while holding its publication lock.
        cancellation_checked = threading.Event()
        allow_store = threading.Event()

        def cancellation_probe():
            cancellation_checked.set()
            allow_store.wait(timeout=2)
            return False

        publishing = threading.Thread(
            target=_store_result,
            args=("k", (), ("fit", "series")),
            kwargs={"cancelled": cancellation_probe},
        )
        publishing.start()
        self.assertTrue(cancellation_checked.wait(timeout=2))

        # When: unload clear races the accepted cache publication.
        cleared = threading.Event()
        clearing = threading.Thread(target=lambda: (clear_results_cache(), cleared.set()))
        clearing.start()
        self.assertFalse(cleared.wait(timeout=0.05))
        allow_store.set()
        publishing.join(timeout=2)
        clearing.join(timeout=2)

        # Then: clear runs after publication and leaves no stale result.
        self.assertTrue(cleared.is_set())
        self.assertNotIn("k", ccd_results)

    def test_lookup_is_atomic_with_cache_clear(self):
        # Given: a lookup paused after reading an entry but before updating its LRU position.
        lookup_read = threading.Event()
        allow_lookup = threading.Event()
        cleared = threading.Event()

        class PausingCache(OrderedDict):
            def get(self, key, default=None):
                value = super().get(key, default)
                lookup_read.set()
                allow_lookup.wait(timeout=2)
                return value

        cache = PausingCache({"k": (("NDVI",), "fit", "series")})
        with (
            patch.object(ccd_process_module, "ccd_results", cache),
            concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor,
        ):
            lookup = executor.submit(lookup_result, "k", ("NDVI",))
            self.assertTrue(lookup_read.wait(timeout=2))

            # When: teardown tries to clear the cache during the compound lookup.
            clearing = executor.submit(lambda: (clear_results_cache(), cleared.set()))
            cleared_during_lookup = cleared.wait(timeout=0.05)
            allow_lookup.set()

            # Then: clear waits for the lookup transaction, which returns without an LRU race.
            self.assertFalse(cleared_during_lookup)
            self.assertEqual(lookup.result(timeout=2), ("fit", "series"))
            clearing.result(timeout=2)

    def test_compute_snapshots_cached_indices_under_lock_before_earth_engine_work(self):
        # Given: cache and Earth Engine seams that record whether the result lock is held.
        class RecordingLock:
            def __init__(self):
                self.held = False

            def __enter__(self):
                self.held = True

            def __exit__(self, _exception_type, _exception, _traceback):
                self.held = False

        lock = RecordingLock()
        cache = Mock()

        def read_cached(_key):
            self.assertTrue(lock.held)
            return None

        def request_earth_engine_data(*_args):
            self.assertFalse(lock.held)
            raise RuntimeError("Earth Engine work reached")

        cache.get.side_effect = read_cached
        fake_ee = types.SimpleNamespace(Geometry=types.SimpleNamespace(Point=lambda coords: coords))
        with (
            patch.dict(sys.modules, {"ee": fake_ee}),
            patch.object(ccd_process_module, "_RESULTS_LOCK", lock),
            patch.object(ccd_process_module, "ccd_results", cache),
            patch.object(ccd_process_module, "get_gee_data_landsat", request_earth_engine_data),
            self.assertRaisesRegex(RuntimeError, "Earth Engine work reached"),
        ):
            compute_ccd(
                coords=(0, 0),
                date_range=("2020-01-01", "2021-01-01"),
                doy_range=(1, 365),
                dataset="Landsat C2",
                breakpoint_bands=("Green", "Red", "NIR", "SWIR1", "SWIR2"),
                tmask_bands=None,
                num_obs=6,
                chi_square=0.99,
                min_years=1.33,
                lambda_lasso=0.002,
            )


def _request_threads():
    return [thread for thread in threading.enumerate() if thread.name == REQUEST_THREAD_NAME]


class CancellationTest(unittest.TestCase):
    def setUp(self):
        clear_results_cache()
        self.addCleanup(clear_results_cache)

    def join_abandoned_requests(self):
        """Let every request a run left behind finish before the test ends."""
        for thread in _request_threads():
            thread.join(timeout=10)

    def compute(self, fake_ee, collection, cancelled):
        with (
            patch.dict(sys.modules, {"ee": fake_ee}),
            patch.object(ccd_process_module, "get_gee_data_landsat", lambda *_: collection),
        ):
            return compute_ccd(
                coords=(0, 0),
                date_range=("2020-01-01", "2021-01-01"),
                doy_range=(1, 365),
                dataset="Landsat C2",
                breakpoint_bands=("Green", "Red", "NIR", "SWIR1", "SWIR2"),
                tmask_bands=None,
                num_obs=6,
                chi_square=0.99,
                min_years=1.33,
                lambda_lasso=0.002,
                cancelled=cancelled,
            )

    def test_cancelled_run_returns_without_waiting_for_earth_engine(self):
        # Given: Earth Engine sitting on a request; getInfo cannot be interrupted.
        requested = threading.Event()
        answer = threading.Event()
        self.addCleanup(answer.set)

        def silent_catalog():
            requested.set()
            answer.wait(timeout=10)
            return CATALOG

        cancel = threading.Event()
        threading.Thread(target=lambda: requested.wait(timeout=10) and cancel.set()).start()
        fake_ee, collection = _fake_earth_engine(catalog=silent_catalog)

        # When: the run is cancelled while that request is in flight.
        started = time.monotonic()
        result = self.compute(fake_ee, collection, cancel.is_set)
        elapsed = time.monotonic() - started

        # Then: it gives up on the request at once instead of holding its task until Earth Engine
        # answers, and nothing reaches the cache.
        self.assertIsNone(result)
        self.assertFalse(answer.is_set())
        self.assertLess(elapsed, 2)
        self.assertEqual(len(ccd_results), 0)

        # And: the request left behind runs on a daemon thread, which cannot hold QGIS from
        # exiting while Earth Engine never answers - getInfo has no deadline by default.
        abandoned = _request_threads()
        self.assertTrue(abandoned)
        self.assertTrue(all(thread.daemon for thread in abandoned))
        answer.set()
        self.join_abandoned_requests()

    def test_abandoned_requests_never_consult_the_task(self):
        # Given: both parallel requests in flight, one of them still unanswered, and a probe
        # recording which threads consult the task's cancellation.
        answer = threading.Event()
        self.addCleanup(answer.set)
        region_requested = threading.Event()

        def silent_region():
            region_requested.set()
            answer.wait(timeout=10)
            return REGION_ROWS

        cancel = threading.Event()
        callers = []

        def cancelled():
            callers.append(threading.current_thread())
            return cancel.is_set()

        threading.Thread(target=lambda: region_requested.wait(timeout=10) and cancel.set()).start()
        fake_ee, collection = _fake_earth_engine(region=silent_region)

        # When: the run is cancelled, returns, and Earth Engine answers afterwards. Once a QGIS
        # task's run returns, the task can be deleted, so touching it then is a use-after-free.
        self.assertIsNone(self.compute(fake_ee, collection, cancelled))
        answer.set()
        self.join_abandoned_requests()

        # Then: only the thread running the task ever consulted it.
        self.assertTrue(callers)
        self.assertEqual(set(callers), {threading.current_thread()})

    def test_completed_run_is_returned_and_cached(self):
        # Given: Earth Engine answering every request.
        fake_ee, collection = _fake_earth_engine(ccdc=lambda: {"tBreak": [[0]]})

        # When: the run completes uncancelled.
        result = self.compute(fake_ee, collection, lambda: False)

        # Then: it returns the fit and the series, and caches them for redraws.
        self.assertIsNotNone(result)
        ccdc_info, timeseries = result
        self.assertEqual(ccdc_info, {"tBreak": [[0]]})
        self.assertEqual(list(timeseries["SWIR1"]), [0.1, 0.2])
        self.assertEqual(len(ccd_results), 1)

    def test_time_series_error_wins_when_both_requests_fail(self):
        # Given: a point with no observations, where the CCDC request fails too, and first.
        ccdc_failed = threading.Event()

        def empty_region():
            ccdc_failed.wait(timeout=10)
            return [REGION_HEADER]

        def failing_ccdc():
            ccdc_failed.set()
            raise RuntimeError("Earth Engine: CCDC failed")

        fake_ee, collection = _fake_earth_engine(region=empty_region, ccdc=failing_ccdc)

        # When/Then: the user sees the message explaining the point, not the CCDC failure.
        with self.assertRaisesRegex(CCDComputationError, "No observations at this point"):
            self.compute(fake_ee, collection, lambda: False)


if __name__ == "__main__":
    unittest.main()
