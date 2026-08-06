import unittest

from core.ccd_process import (
    DATASET_AVAILABILITY,
    _no_images_message,
    _store_result,
    ccd_results,
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
        ccd_results.clear()
        self.addCleanup(ccd_results.clear)

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


if __name__ == "__main__":
    unittest.main()
