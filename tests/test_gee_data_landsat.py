import sys
import types
import unittest

from core.gee_common import (
    CCD_BANDS,
    INDEX_RANGE,
    OPTICAL_BANDS,
    add_indices,
    date_and_doy_filter,
    resolve_indices,
)
from core.gee_data_landsat import SENSORS, TC_OLI, TC_TM


def _restore_module(name, previous):
    """Put a stubbed module back, removing the entry entirely when there was nothing there.

    Assigning None would leave a poisoned entry: a later `import ee` raises "import of ee halted;
    None in sys.modules" instead of the real ImportError.
    """
    if previous is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = previous


class FakeBand:
    """Records the naming/casting/clamping of one derived band through a chain of band maths."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.cast_count = 0
        self.clamped_to: tuple[float, float] | None = None
        self.masked_by: FakeBand | None = None
        self._multiplied_by = None

    def rename(self, name: str) -> "FakeBand":
        self.name = name
        return self

    def toFloat(self) -> "FakeBand":
        self.cast_count += 1
        return self

    def clamp(self, low: float, high: float) -> "FakeBand":
        self.clamped_to = (low, high)
        return self

    # Arithmetic yields a fresh recorder, so two indices derived from the same source band do not
    # end up sharing (and overwriting) one another's name.
    def _derived(self) -> "FakeBand":
        derived = FakeBand(self.name)
        # carry the recorded weights through reduce()/mask() so the whole chain can be asserted
        derived._multiplied_by = self._multiplied_by
        return derived

    def multiply_args(self):
        """The coefficients this band was last weighted by."""
        return self._multiplied_by

    def multiply(self, other) -> "FakeBand":
        derived = self._derived()
        derived._multiplied_by = other
        return derived

    def subtract(self, other) -> "FakeBand":
        return self._derived()

    def divide(self, other) -> "FakeBand":
        return self._derived()

    def add(self, other) -> "FakeBand":
        return self._derived()

    def reduce(self, reducer) -> "FakeBand":
        return self._derived()

    def mask(self) -> "FakeBand":
        return self._derived()

    def updateMask(self, other) -> "FakeBand":
        self.masked_by = other
        return self


class FakeImage:
    def __init__(self) -> None:
        self.added_bands: list[FakeBand] = []

    def select(self, bands) -> FakeBand:
        return FakeBand(bands if isinstance(bands, str) else "stack")

    def normalizedDifference(self, bands: list[str]) -> FakeBand:
        return FakeBand("normalizedDifference")

    def addBands(self, bands: list[FakeBand]) -> "FakeImage":
        self.added_bands = list(bands)
        return self


class AddIndicesTest(unittest.TestCase):
    def setUp(self):
        # add_indices imports ee lazily; it needs Reducer.sum() for the weighted sum and
        # Reducer.min() to rebuild the "every input band valid" mask
        module = types.ModuleType("ee")
        module.Reducer = types.SimpleNamespace(sum=lambda: "sum", min=lambda: "min")
        self.addCleanup(_restore_module, "ee", sys.modules.get("ee"))
        sys.modules["ee"] = module

    def test_adds_every_band_the_common_schema_promises(self):
        # Given: a fake image and the TM tasseled-cap coefficients.
        fake_image = FakeImage()

        # When: indices are added to the image.
        add_indices(fake_image, TC_TM)

        # Then: exactly the index half of the shared schema is produced.
        self.assertEqual([band.name for band in fake_image.added_bands], list(CCD_BANDS[6:]))

    def test_sensor_specific_tasseled_cap_bands_are_cast_before_add_bands(self):
        # Given: a fake image and the TM tasseled-cap coefficients.
        fake_image = FakeImage()

        # When: indices are added to the image.
        add_indices(fake_image, TC_TM)

        # Then: every tasseled-cap band was cast to float exactly once.
        added = {band.name: band for band in fake_image.added_bands}
        for band_name in ("BRIGHTNESS", "GREENNESS", "WETNESS"):
            self.assertEqual(added[band_name].cast_count, 1)

    def test_tasseled_cap_is_masked_where_any_input_band_is(self):
        # Given: a fake image.
        fake_image = FakeImage()

        # When: indices are added.
        add_indices(fake_image, TC_TM)

        # Then: every tasseled cap band re-applies a validity mask. ee.Reducer.sum() drops masked
        # bands instead of propagating them, so without this a pixel missing one band would come
        # back as a short weighted sum rather than masked.
        added = {band.name: band for band in fake_image.added_bands}
        for band_name in ("BRIGHTNESS", "GREENNESS", "WETNESS"):
            with self.subTest(band=band_name):
                self.assertIsNotNone(added[band_name].masked_by)

    def test_tasseled_cap_weights_the_optical_stack_in_band_order(self):
        # Given: a fake image. The transform is a positional weighted sum over
        # select(OPTICAL_BANDS), so each component must be handed its own coefficient list.
        fake_image = FakeImage()

        # When: indices are added.
        add_indices(fake_image, TC_TM)

        # Then: each component multiplied the stack by its own coefficients, in band order.
        added = {band.name: band for band in fake_image.added_bands}
        for band_name in ("BRIGHTNESS", "GREENNESS", "WETNESS"):
            with self.subTest(band=band_name):
                self.assertEqual(added[band_name].multiply_args(), TC_TM[band_name])
                self.assertEqual(len(TC_TM[band_name]), len(OPTICAL_BANDS))

    def test_ratio_indices_are_clamped_but_normalized_differences_are_not(self):
        # Given: a fake image.
        fake_image = FakeImage()

        # When: indices are added to the image.
        add_indices(fake_image, TC_TM)

        # Then: only EVI/EVI2, whose denominator can approach zero, are bounded.
        added = {band.name: band for band in fake_image.added_bands}
        self.assertEqual(added["EVI"].clamped_to, INDEX_RANGE)
        self.assertEqual(added["EVI2"].clamped_to, INDEX_RANGE)
        self.assertIsNone(added["NDVI"].clamped_to)
        self.assertIsNone(added["NBR"].clamped_to)


class TasseledCapTest(unittest.TestCase):
    def test_every_component_has_one_coefficient_per_optical_band(self):
        # Given: both coefficient sets. The transform is a weighted sum over select(OPTICAL_BANDS),
        # so the coefficient order *is* the band order - a short or long list would silently
        # pair weights with the wrong bands.
        for coefficients in (TC_TM, TC_OLI):
            for component in ("BRIGHTNESS", "GREENNESS", "WETNESS"):
                with self.subTest(component=component):
                    self.assertEqual(len(coefficients[component]), len(OPTICAL_BANDS))

    def test_sensor_handover_cannot_manufacture_a_break(self):
        # Given: representative Blue..SWIR2 surface reflectance for the covers CCDC is run over.
        spectra = {
            "dense forest": [0.02, 0.04, 0.025, 0.32, 0.13, 0.05],
            "pasture": [0.05, 0.08, 0.09, 0.28, 0.25, 0.14],
            "bare soil": [0.10, 0.14, 0.20, 0.28, 0.35, 0.28],
            "water": [0.03, 0.035, 0.02, 0.008, 0.004, 0.003],
        }

        # When: the TM/ETM+ and the OLI/OLI-2 transforms are applied to identical reflectance.
        # Then: the two land within the noise of a stable series, so an L5/L7 observation and an
        # L8/L9 observation of the same target sit on the same curve.
        for cover, reflectance in spectra.items():
            for component in ("BRIGHTNESS", "GREENNESS", "WETNESS"):
                tm_value = sum(c * r for c, r in zip(TC_TM[component], reflectance, strict=True))
                oli_value = sum(c * r for c, r in zip(TC_OLI[component], reflectance, strict=True))
                with self.subTest(cover=cover, component=component):
                    self.assertAlmostEqual(tm_value, oli_value, delta=0.002)

    def test_only_two_coefficient_sets_are_in_use(self):
        # Given: every configured sensor.
        # When: the distinct coefficient sets are collected.
        used = {id(spec.tc_coefficients) for spec in SENSORS}

        # Then: TM/ETM+ share one set and OLI/OLI-2 the other.
        self.assertEqual(used, {id(TC_TM), id(TC_OLI)})


class RequestedIndicesTest(unittest.TestCase):
    def setUp(self):
        module = types.ModuleType("ee")
        module.Reducer = types.SimpleNamespace(sum=lambda: "sum", min=lambda: "min")
        self.addCleanup(_restore_module, "ee", sys.modules.get("ee"))
        sys.modules["ee"] = module

    def test_only_the_requested_indices_are_built(self):
        # Given: a run that needs one index.
        fake_image = FakeImage()

        # When: indices are added for that subset.
        add_indices(fake_image, TC_TM, ["NDVI"])

        # Then: nothing else is computed.
        self.assertEqual([band.name for band in fake_image.added_bands], ["NDVI"])

    def test_no_requested_indices_leaves_the_image_untouched(self):
        # Given: the default configuration, whose bands are all optical.
        fake_image = FakeImage()

        # When: no index is requested.
        result = add_indices(fake_image, TC_TM, [])

        # Then: the image is returned as-is, with no bands added at all.
        self.assertIs(result, fake_image)
        self.assertEqual(fake_image.added_bands, [])

    def test_requested_indices_keep_canonical_order(self):
        # Given: indices asked for out of order.
        fake_image = FakeImage()

        # When: they are added.
        add_indices(fake_image, TC_TM, ["WETNESS", "NDVI", "EVI"])

        # Then: the band order follows the shared schema, not the request order.
        self.assertEqual([band.name for band in fake_image.added_bands], ["NDVI", "EVI", "WETNESS"])

    def test_resolve_indices_drops_the_optical_bands(self):
        # Given: a mixed band selection.
        # When: the index half is resolved.
        # Then: only indices survive, in schema order.
        self.assertEqual(resolve_indices(["SWIR1", "NBR", "Green", "NDVI"]), ("NDVI", "NBR"))
        self.assertEqual(resolve_indices(["Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2"]), ())


class FakeFilter:
    """Records how ee.Filter would have been assembled, without importing ee."""

    def __init__(self, kind, *args):
        self.kind = kind
        self.args = args


class DayOfYearFilterTest(unittest.TestCase):
    def setUp(self):
        import sys
        import types

        module = types.ModuleType("ee")
        module.Date = lambda value: value
        module.Filter = types.SimpleNamespace(
            date=lambda start, end: FakeFilter("date", start, end),
            dayOfYear=lambda start, end: FakeFilter("doy", start, end),
            And=lambda *args: FakeFilter("and", *args),
            Or=lambda *args: FakeFilter("or", *args),
        )
        self.addCleanup(_restore_module, "ee", sys.modules.get("ee"))
        sys.modules["ee"] = module

    def test_forward_window_uses_a_single_day_of_year_filter(self):
        # Given: a day-of-year window that does not cross the new year.
        # When: the filter is built.
        combined = date_and_doy_filter(("2020-01-01", "2021-01-01"), (150, 250))

        # Then: one dayOfYear range covers it.
        doy_filter = combined.args[1]
        self.assertEqual(doy_filter.kind, "doy")
        self.assertEqual(doy_filter.args, (150, 250))

    def test_whole_year_window_drops_the_day_of_year_test_entirely(self):
        # Given: the default window, which places no seasonal restriction at all.
        # When: the filter is built.
        combined = date_and_doy_filter(("2020-01-01", "2021-01-01"), (1, 365))

        # Then: only the date filter remains - no per-scene day-of-year test to evaluate.
        self.assertEqual(combined.kind, "date")

    def test_window_wrapping_the_new_year_becomes_a_union(self):
        # Given: a southern-hemisphere dry season that crosses the new year.
        # When: the filter is built.
        combined = date_and_doy_filter(("2020-01-01", "2021-01-01"), (300, 60))

        # Then: it is split into two ranges instead of matching nothing.
        doy_filter = combined.args[1]
        self.assertEqual(doy_filter.kind, "or")
        self.assertEqual([part.args for part in doy_filter.args], [(300, 366), (1, 60)])


if __name__ == "__main__":
    unittest.main()
