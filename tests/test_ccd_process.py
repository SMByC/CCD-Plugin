import concurrent.futures
import sys
import threading
import types
import unittest
from collections import OrderedDict
from unittest.mock import Mock, patch

import core.ccd_process as ccd_process_module
from core.ccd_process import (
    DATASET_AVAILABILITY,
    _no_images_message,
    _store_result,
    ccd_results,
    clear_results_cache,
    compute_ccd,
    lookup_result,
    resolve_computed_indices,
)


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


if __name__ == "__main__":
    unittest.main()
