import unittest

from core.gee_common import CCD_BANDS, INDEX_RANGE, add_indices, date_and_doy_filter, tc_expression
from core.gee_data_landsat import SENSORS, TC_OLI, TC_TM


class FakeBand:
    def __init__(self, name: str = "") -> None:
        self.name = name
        self.cast_count = 0
        self.clamped_to: tuple[float, float] | None = None

    def rename(self, name: str) -> "FakeBand":
        self.name = name
        return self

    def toFloat(self) -> "FakeBand":
        self.cast_count += 1
        return self

    def clamp(self, low: float, high: float) -> "FakeBand":
        self.clamped_to = (low, high)
        return self


class FakeImage:
    def __init__(self) -> None:
        self.added_bands: list[FakeBand] = []
        self.expressions: list[str] = []

    def select(self, band: str) -> FakeBand:
        return FakeBand(band)

    def normalizedDifference(self, bands: list[str]) -> FakeBand:
        return FakeBand("normalizedDifference")

    def expression(self, expression: str, bands: dict[str, FakeBand]) -> FakeBand:
        self.expressions.append(expression)
        return FakeBand("expression")

    def addBands(self, bands: list[FakeBand]) -> "FakeImage":
        self.added_bands = list(bands)
        return self


class AddIndicesTest(unittest.TestCase):
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
    def test_expression_pairs_each_coefficient_with_its_band(self):
        # Given: the TM brightness coefficients.
        # When: the tasseled-cap expression is built.
        expression = tc_expression(TC_TM["BRIGHTNESS"])

        # Then: coefficients are applied in the documented band order.
        self.assertEqual(
            expression,
            "(0.3037) * Blue + (0.2793) * Green + (0.4743) * Red "
            "+ (0.5585) * NIR + (0.5082) * SWIR1 + (0.1863) * SWIR2",
        )

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
        self.addCleanup(sys.modules.pop, "ee", None)
        sys.modules["ee"] = module

    def test_forward_window_uses_a_single_day_of_year_filter(self):
        # Given: a day-of-year window that does not cross the new year.
        # When: the filter is built.
        combined = date_and_doy_filter(("2020-01-01", "2021-01-01"), (150, 250))

        # Then: one dayOfYear range covers it.
        doy_filter = combined.args[1]
        self.assertEqual(doy_filter.kind, "doy")
        self.assertEqual(doy_filter.args, (150, 250))

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
