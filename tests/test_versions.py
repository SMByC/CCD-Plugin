import unittest

from utils.versions import MIN_PLOTLY_VERSION, version_satisfies, version_tuple


class VersionTest(unittest.TestCase):
    def test_release_numbers_are_parsed(self):
        self.assertEqual(version_tuple("5.16.0"), (5, 16, 0))
        self.assertEqual(version_tuple("6.0.0rc1"), (6, 0, 0))
        self.assertEqual(version_tuple("5.16"), (5, 16))
        self.assertEqual(version_tuple(""), ())

    def test_plotly_floor_is_the_first_version_with_shapes_in_the_legend(self):
        # 5.15 fails building the figure with "Invalid property ... legendgroup"; 5.16 builds it
        self.assertEqual(MIN_PLOTLY_VERSION, (5, 16))
        self.assertFalse(version_satisfies("5.15.0", MIN_PLOTLY_VERSION))
        self.assertTrue(version_satisfies("5.16.0", MIN_PLOTLY_VERSION))
        self.assertTrue(version_satisfies("6.7.0", MIN_PLOTLY_VERSION))
        self.assertFalse(version_satisfies("unknown", MIN_PLOTLY_VERSION))


if __name__ == "__main__":
    unittest.main()
