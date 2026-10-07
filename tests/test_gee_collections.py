import sys
import types
import unittest
from unittest.mock import patch

import core.gee_data_landsat as landsat
import core.gee_data_sentinel as sentinel
from core.gee_common import OPTICAL_BANDS


class Chain:
    """Any Earth Engine object: every method call is logged and returns the same object."""

    def __init__(self, log, name="image"):
        self.log = log
        self.name = name

    def __getattr__(self, method):
        def call(*args, **kwargs):
            self.log.append((self.name, method))
            return self

        return call


class FakeCollection:
    """An image collection whose map() runs the mapped function on one image right away."""

    def __init__(self, log):
        self.log = log

    def map(self, function):
        function(Chain(self.log))
        return self

    def select(self, bands):
        return self

    def sort(self, prop):
        return self


def _fake_ee():
    return types.SimpleNamespace(
        Geometry=types.SimpleNamespace(Point=lambda coords: coords),
        Image=lambda value: value,
        Reducer=types.SimpleNamespace(min=lambda: "min", max=lambda: "max"),
    )


class ValidityRuleIsAppliedTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(sys.modules, {"ee": _fake_ee()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = []

    def validity(self):
        def record(stack):
            self.log.append(("rule", "valid_reflectance"))
            return Chain(self.log, "valid")

        return record

    def test_landsat_observations_are_masked_by_the_shared_rule(self):
        # Given: a Landsat scene, and the shared validity rule recorded.
        with patch.object(landsat, "valid_reflectance", side_effect=self.validity()) as rule:
            # When: it is prepared.
            landsat.prepare_image(Chain(self.log), landsat.SENSORS[3])

        # Then: the rule built its mask, from the scaled optical bands - not a dataset's own floor.
        rule.assert_called_once()
        self.assertFalse(hasattr(landsat, "SR_MIN"))

    def test_sentinel_validity_is_applied_after_the_cloud_mask(self):
        # Given: the Sentinel-2 pipeline, its cloud mask and the validity rule recorded.
        def cloud_mask(collection):
            self.log.append(("cloud", "Sen2Cor"))
            return collection

        with (
            patch.object(sentinel, "filter_collection", return_value=FakeCollection(self.log)),
            patch.object(sentinel, "apply_sen2cor", side_effect=cloud_mask),
            patch.object(sentinel, "add_indices", side_effect=lambda image, *_: image),
            patch.object(sentinel, "valid_reflectance", side_effect=self.validity()),
        ):
            # When: a series is built.
            sentinel.get_gee_data_sentinel((0, 0), ("2020-01-01", "2021-01-01"), (1, 365), "Sentinel-2", "Sen2Cor", ())

        # Then: the rule masks the observations once the cloud mask is done: masked first, the
        # brightest cloud pixels did not seed the buffer the cloud mask grows around them.
        self.assertLess(self.log.index(("cloud", "Sen2Cor")), self.log.index(("rule", "valid_reflectance")))
        self.assertEqual(self.log.count(("rule", "valid_reflectance")), 1)

    def test_sentinel_bands_are_scaled_without_masking(self):
        # Given/When: one Sentinel-2 scene scaled into the common schema.
        with patch.object(sentinel, "valid_reflectance", side_effect=self.validity()) as rule:
            sentinel.prepare_bands(Chain(self.log))

        # Then: nothing is masked yet, see test_sentinel_validity_is_applied_after_the_cloud_mask.
        rule.assert_not_called()
        self.assertNotIn(("image", "updateMask"), self.log)

    def test_the_rule_reads_the_optical_bands(self):
        # Given: a scene once cloud masked.
        selected = []
        image = types.SimpleNamespace(
            select=lambda bands: selected.append(bands) or "optical",
            updateMask=lambda mask: mask,
        )

        # When: the validity rule is applied to it.
        with patch.object(sentinel, "valid_reflectance", side_effect=lambda stack: ("valid", stack)):
            mask = sentinel.keep_valid_reflectance(image)

        # Then: it is built from the six optical bands.
        self.assertEqual(selected, [list(OPTICAL_BANDS)])
        self.assertEqual(mask, ("valid", "optical"))


if __name__ == "__main__":
    unittest.main()
