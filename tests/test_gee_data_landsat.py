import sys
import types
import unittest

from core.gee_common import (
    CCD_BANDS,
    INDEX_BANDS,
    INDEX_RANGE,
    INDEX_SOURCES,
    OPTICAL_BANDS,
    REFLECTANCE_RANGE,
    add_indices,
    date_and_doy_filter,
    resolve_indices,
    valid_reflectance,
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

    def __init__(self, name: str = "", floors: list | None = None) -> None:
        self.name = name
        self.cast_count = 0
        self.clamped_to: tuple[float, float] | None = None
        self.masked_by: FakeBand | None = None
        self._multiplied_by = None
        # (band, floor) for every max() taken on the way, shared by the whole chain
        self.floors = floors if floors is not None else []

    def max(self, other) -> "FakeBand":
        self.floors.append((self.name, other))
        return self._derived()

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
        derived = FakeBand(self.name, self.floors)
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
        self.floors: list = []

    def select(self, bands) -> FakeBand:
        return FakeBand(bands if isinstance(bands, str) else "stack", self.floors)

    def normalizedDifference(self, bands: list[str]) -> FakeBand:
        raise AssertionError("normalizedDifference masks negative inputs, dropping the observation from CCDC")

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

    def test_no_index_masks_a_pixel_the_optical_bands_keep(self):
        # Given: a fake image whose normalizedDifference refuses to run: it masks negative inputs,
        # and CCDC drops a whole observation when any band of it is masked.
        fake_image = FakeImage()

        # When: every index is added.
        add_indices(fake_image, TC_TM)

        # Then: no ratio index carries a mask of its own, so plotting one cannot change the fit.
        added = {band.name: band for band in fake_image.added_bands}
        for name in ("NDVI", "NBR", "EVI", "EVI2"):
            with self.subTest(index=name):
                self.assertIsNone(added[name].masked_by)

    def test_ratio_indices_are_computed_from_inputs_floored_at_zero(self):
        # Given: a fake image.
        fake_image = FakeImage()

        # When: the ratio indices are added.
        add_indices(fake_image, TC_TM, ["NDVI", "NBR", "EVI", "EVI2"])

        # Then: every input they read is floored at zero, which keeps them defined and in range.
        self.assertEqual(set(fake_image.floors), {("NIR", 0), ("Red", 0), ("Blue", 0), ("SWIR2", 0)})

    def test_every_index_is_cast_to_plain_float(self):
        # Given: a fake image. CCDC needs a homogeneous collection, and the value range Earth
        # Engine infers for an arithmetic result differs between sensors.
        fake_image = FakeImage()

        # When: every index is added.
        add_indices(fake_image, TC_TM)

        # Then: each one is cast to float exactly once.
        for band in fake_image.added_bands:
            with self.subTest(index=band.name):
                self.assertEqual(band.cast_count, 1)

    def test_every_index_names_its_optical_sources(self):
        # Given/When/Then: the redundancy check knows what each index is computed from.
        self.assertEqual(set(INDEX_SOURCES), set(INDEX_BANDS))
        for name, sources in INDEX_SOURCES.items():
            with self.subTest(index=name):
                self.assertLessEqual(set(sources), set(OPTICAL_BANDS))


class FakeReduction:
    """One reduction of a fake optical stack, recording the comparisons made on it."""

    def __init__(self, reducer, calls):
        self.reducer = reducer
        self.calls = calls

    def gt(self, value):
        self.calls.append((self.reducer, "gt", value))
        return self

    def lte(self, value):
        self.calls.append((self.reducer, "lte", value))
        return self

    def And(self, other):
        return self


class ValidReflectanceTest(unittest.TestCase):
    def setUp(self):
        module = types.ModuleType("ee")
        module.Reducer = types.SimpleNamespace(min=lambda: "min", max=lambda: "max")
        self.addCleanup(_restore_module, "ee", sys.modules.get("ee"))
        sys.modules["ee"] = module

    def test_every_band_must_be_strictly_positive_and_at_most_one(self):
        # Given: a fake optical stack.
        calls = []
        stack = types.SimpleNamespace(reduce=lambda reducer: FakeReduction(reducer, calls))

        # When: the validity mask is built.
        valid_reflectance(stack)

        # Then: the darkest band must be above 0, as the reference CCDC implementations require,
        # and the brightest at most 1.
        self.assertEqual(REFLECTANCE_RANGE, (0.0, 1.0))
        self.assertEqual(sorted(calls), [("max", "lte", 1.0), ("min", "gt", 0.0)])


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


class FakeDate:
    """Records an ee.Date and the offsets applied to it."""

    def __init__(self, value, offsets=()):
        self.value = value
        self.offsets = offsets

    def advance(self, delta, unit):
        return FakeDate(self.value, (*self.offsets, (delta, unit)))

    def __eq__(self, other):
        return isinstance(other, FakeDate) and (self.value, self.offsets) == (other.value, other.offsets)

    def __repr__(self):
        return f"FakeDate({self.value!r}, {self.offsets!r})"


class DayOfYearFilterTest(unittest.TestCase):
    def setUp(self):
        module = types.ModuleType("ee")
        module.Date = FakeDate
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

    def test_the_end_date_is_included(self):
        # Given: a range the date controls present as inclusive at both ends.
        # When: the filter is built.
        combined = date_and_doy_filter(("2020-01-01", "2020-12-31"), (1, 365))

        # Then: Earth Engine's exclusive end is moved past the last selected day, so the
        # observations of 31 December - and a single-day range - are kept.
        self.assertEqual(combined.args, (FakeDate("2020-01-01"), FakeDate("2020-12-31", ((1, "day"),))))

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
