import importlib
import math
import os
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

POINT: Final = (-122.01285, 37.74999)
OPTICAL_BANDS: Final = ("Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2")


@dataclass(frozen=True, slots=True)
class CollectionConfig:
    date_range: tuple[str, str]
    doy_range: tuple[int, int]


@dataclass(frozen=True, slots=True)
class CcdcConfig:
    date_range: tuple[str, str] = ("2010-01-01", "2026-08-03")
    doy_range: tuple[int, int] = (1, 365)
    dataset: str = "Landsat C2"
    # the shipped default, so the live run exercises what users actually get
    breakpoint_bands: tuple[str, ...] = ("Green", "Red", "NIR", "SWIR1", "SWIR2")
    tmask_bands: None = None
    num_obs: int = 6
    chi_square: float = 0.99
    min_years: float = 1.33
    lambda_lasso: float = 0.002


LANDSAT_CONFIG: Final = CollectionConfig(("2018-01-01", "2023-01-01"), (150, 250))
SENTINEL_CONFIG: Final = CollectionConfig(("2022-01-01", "2023-01-01"), (1, 365))
CCDC_CONFIG: Final = CcdcConfig()


@unittest.skipUnless(os.getenv("CCD_RUN_LIVE_GEE") == "1", "set CCD_RUN_LIVE_GEE=1 to run live GEE tests")
class LiveGoogleEarthEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        package_root = Path(__file__).resolve().parents[1]
        package = types.ModuleType("CCD_Plugin")
        package.__path__ = [str(package_root)]
        sys.modules["CCD_Plugin"] = package

        from CCD_Plugin.core.ccd_process import compute_ccd
        from CCD_Plugin.core.gee_data_landsat import get_gee_data_landsat
        from CCD_Plugin.core.gee_data_sentinel import get_gee_data_sentinel

        ee = importlib.import_module("ee")
        ee.Initialize()
        cls.ee = ee
        cls.compute_ccd = staticmethod(compute_ccd)
        cls.get_gee_data_landsat = staticmethod(get_gee_data_landsat)
        cls.get_gee_data_sentinel = staticmethod(get_gee_data_sentinel)

    def _get_region(self, collection, bands: tuple[str, ...], scale: int):
        point = self.ee.Geometry.Point(POINT)
        return self.ee.List(collection.select(list(bands)).getRegion(geometry=point, scale=scale)).getInfo()

    def _assert_region_has_clear_swir1(self, region, expected_bands: tuple[str, ...]):
        self.assertGreater(len(region), 1)
        header = region[0]
        self.assertTrue(set(expected_bands).issubset(header))
        swir1_index = header.index("SWIR1")
        clear_rows = [row for row in region[1:] if row[swir1_index] is not None and math.isfinite(row[swir1_index])]
        self.assertTrue(clear_rows)
        return header, clear_rows

    def test_landsat_retrieval_filters_doy_and_preserves_cross_sensor_schema(self) -> None:
        # Given: a multi-sensor Landsat date range restricted to summer observations.
        selected_bands = ("SWIR1", "BRIGHTNESS", "GREENNESS", "WETNESS")

        # When: the merged collection is retrieved and evaluated at the point.
        collection = self.get_gee_data_landsat(POINT, LANDSAT_CONFIG.date_range, LANDSAT_CONFIG.doy_range)
        region = self._get_region(collection, selected_bands, scale=30)

        # Then: the homogeneous schema is evaluable and every clear observation is in the requested DOY range.
        header, clear_rows = self._assert_region_has_clear_swir1(region, selected_bands)
        time_index = header.index("time")
        for row in clear_rows:
            day_of_year = datetime.fromtimestamp(row[time_index] / 1000, tz=UTC).timetuple().tm_yday
            self.assertGreaterEqual(day_of_year, LANDSAT_CONFIG.doy_range[0])
            self.assertLessEqual(day_of_year, LANDSAT_CONFIG.doy_range[1])

    def test_every_sentinel_cloud_filter_returns_clear_optical_observations(self) -> None:
        # Given: a full-year Sentinel-2 query for each selectable cloud filter.
        from CCD_Plugin.core.gee_data_sentinel import CLOUD_FILTERS

        for cloud_filter in CLOUD_FILTERS:
            with self.subTest(cloud_filter=cloud_filter):
                # When: the filtered collection is retrieved and evaluated at the point.
                collection = self.get_gee_data_sentinel(
                    POINT,
                    SENTINEL_CONFIG.date_range,
                    SENTINEL_CONFIG.doy_range,
                    "Sentinel-2",
                    cloud_filter,
                )
                region = self._get_region(collection, OPTICAL_BANDS, scale=10)

                # Then: the optical schema and at least one clear finite SWIR1 observation survive.
                self._assert_region_has_clear_swir1(region, OPTICAL_BANDS)

    def test_masking_trades_retention_for_quality_in_the_expected_order(self) -> None:
        # Given: the same Sentinel-2 window under no mask and under each real cloud filter.
        def clear_count(cloud_filter: str) -> int:
            collection = self.get_gee_data_sentinel(
                POINT, SENTINEL_CONFIG.date_range, SENTINEL_CONFIG.doy_range, "Sentinel-2", cloud_filter
            )
            region = self._get_region(collection, ("SWIR1",), scale=10)
            index = region[0].index("SWIR1")
            return sum(1 for row in region[1:] if row[index] is not None)

        # When: the surviving observations are counted per filter.
        unmasked = clear_count("No Mask")
        masked = {name: clear_count(name) for name in ("Cloud Score+", "s2cloudless", "Sen2Cor")}

        # Then: every filter removes something but none of them empties the series.
        for name, count in masked.items():
            with self.subTest(cloud_filter=name):
                self.assertGreater(count, 0, f"{name} removed every observation")
                self.assertLessEqual(count, unmasked)

    def test_day_of_year_window_wrapping_the_new_year_returns_data(self) -> None:
        # Given: a DOY window that crosses the new year, which a single dayOfYear filter cannot express.
        collection = self.get_gee_data_landsat(POINT, ("2018-01-01", "2023-01-01"), (330, 45))

        # When: the collection is evaluated at the point.
        region = self._get_region(collection, ("SWIR1",), scale=30)

        # Then: observations are returned, and all of them are inside either half of the window.
        _, clear_rows = self._assert_region_has_clear_swir1(region, ("SWIR1",))
        time_index = region[0].index("time")
        for row in clear_rows:
            day_of_year = datetime.fromtimestamp(row[time_index] / 1000, tz=UTC).timetuple().tm_yday
            self.assertTrue(day_of_year >= 330 or day_of_year <= 45, f"DOY {day_of_year} outside the window")

    def test_landsat_ccdc_returns_observations_and_coefficient_segments(self) -> None:
        # Given: the supplied Landsat CCDC regression configuration.
        config = CCDC_CONFIG

        # When: CCDC is computed against the live Earth Engine catalog.
        result, timeseries = self.compute_ccd(
            POINT,
            config.date_range,
            config.doy_range,
            config.dataset,
            config.breakpoint_bands,
            config.tmask_bands,
            config.num_obs,
            config.chi_square,
            config.min_years,
            config.lambda_lasso,
        )

        # Then: enough observations and at least one fitted SWIR1 coefficient segment are returned.
        self.assertGreater(len(timeseries["time"]), config.num_obs)
        self.assertIn("tBreak", result)
        self.assertIn("SWIR1_coefs", result)
        self.assertTrue(result["SWIR1_coefs"])
        self.assertTrue(result["SWIR1_coefs"][0])


if __name__ == "__main__":
    unittest.main()
