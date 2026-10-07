import unittest

from core.coordinates import COORDINATE_DECIMALS, normalize_longitude


class NormalizeLongitudeTest(unittest.TestCase):
    def test_longitudes_inside_the_range_are_unchanged(self):
        for longitude in (-180.0, -75.123456, 0.0, 179.9999999):
            with self.subTest(longitude=longitude):
                self.assertAlmostEqual(normalize_longitude(longitude), longitude, places=9)

    def test_a_canvas_panned_past_the_antimeridian_wraps_around(self):
        # Given: clicks on a geographic canvas east and west of the dateline, beyond +-180.
        # Then: they land on the meridians they show, instead of being clamped to 180.
        self.assertAlmostEqual(normalize_longitude(190.0), -170.0)
        self.assertAlmostEqual(normalize_longitude(-190.0), 170.0)
        self.assertAlmostEqual(normalize_longitude(540.0), -180.0)

    def test_coordinates_keep_centimetre_precision(self):
        # 1e-7 degree is ~1 cm, well under the 10 m pixel the point is sampled from
        self.assertGreaterEqual(COORDINATE_DECIMALS, 7)


if __name__ == "__main__":
    unittest.main()
