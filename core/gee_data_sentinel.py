"""
/***************************************************************************
 CCDC at Point Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2023-2026 by Daniel Moraes
        email                : moraesd90@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/

"""

from typing import Final

from .gee_common import INDEX_BANDS, OPTICAL_BANDS, add_indices, filter_collection, resolve_indices

S2_SR: Final = "COPERNICUS/S2_SR_HARMONIZED"
S2_CLOUD_PROBABILITY: Final = "COPERNICUS/S2_CLOUD_PROBABILITY"
CLOUD_SCORE_PLUS: Final = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"

# L2A bands matching the common optical schema; B8 is the 10 m NIR, B11/B12 the 20 m SWIR.
S2_BANDS: Final = ("B2", "B3", "B4", "B8", "B11", "B12")
S2_SCALE: Final = 0.0001

# S2_SR_HARMONIZED shifts processing-baseline 04.00 scenes (2022-01-25 onward) back by the
# BOA_ADD_OFFSET of -1000 DN so they line up radiometrically with earlier scenes. Scenes before
# that date were clipped at DN 0 and can never go negative; later scenes reach DN -1000, i.e.
# -0.1 reflectance, for the very same target. Any floor tighter than -0.1 would therefore mask
# dark observations in the later era only and hand CCDC an artificial break at the baseline
# switch, so -0.1 is the only value that treats both eras alike.
S2_MIN: Final = -0.1
# Surface reflectance above 1 is not physical; it is residual bright cloud the mask did not catch.
S2_MAX: Final = 1.0

# Scene classification (SCL) classes that are never usable: 0 no data, 1 saturated/defective,
# 3 cloud shadow, 8 cloud medium probability, 9 cloud high probability, 10 thin cirrus, 11 snow.
# Classes 2 (dark area), 4 (vegetation), 5 (not vegetated), 6 (water) and 7 (unclassified) are
# kept: dropping them (as an allow-list of 4/5/6 does) discards legitimate dark surfaces,
# terrain shadow and any pixel Sen2Cor could not label, which over broken terrain removes most
# of the series for no quality gain.
SCL_REJECTED: Final = (0, 1, 3, 8, 9, 10, 11)
SCL_SCALE: Final = 20

# Cloud Score+ 'cs_cdf' is a 0-1 clear-confidence score; Google suggests 0.50-0.65 for "clear".
CS_PLUS_BAND: Final = "cs_cdf"
CS_PLUS_THRESHOLD: Final = 0.60

# s2cloudless tuning (Earth Engine community cloud-masking recipe)
CLOUD_PROBABILITY_THRESHOLD: Final = 50
DARK_NIR_THRESHOLD: Final = 0.15
CLOUD_PROJECTION_DISTANCE_KM: Final = 1
CLOUD_BUFFER_METERS: Final = 50

# Order matches the combo box in ui/advanced_settings.ui; the first entry is the default there too.
CLOUD_FILTERS: Final = ("Cloud Score+", "s2cloudless", "Sen2Cor", "No Mask")
DEFAULT_CLOUD_FILTER: Final = CLOUD_FILTERS[0]

# Tasseled cap for Sentinel-2 MSI - Shi, T. & Xu, H. (2019). Derivation of tasseled cap
# transformation coefficients for Sentinel-2 MSI at-sensor reflectance data. IEEE JSTARS,
# 12(10), 4038-4048. https://doi.org/10.1109/JSTARS.2019.2938388
# Note: derived for at-sensor reflectance and applied here to L2A surface reflectance, so the
# absolute values carry a bias. It is constant in time for a single sensor, so it shifts the
# CCDC intercept without creating breaks; there is no published SR-domain equivalent in wide use.
TC_S2: Final = {
    "BRIGHTNESS": [0.3510, 0.3813, 0.3437, 0.7196, 0.2396, 0.1949],
    "GREENNESS": [-0.3599, -0.3533, -0.4734, 0.6633, 0.0087, -0.2856],
    "WETNESS": [0.2578, 0.2305, 0.0883, 0.1071, -0.7611, -0.5308],
}


def prepare_bands(image):
    """Scale the L2A bands into the common optical schema, keeping SCL for the cloud masks."""
    import ee

    scaled = image.select(list(S2_BANDS)).multiply(S2_SCALE).rename(list(OPTICAL_BANDS))
    in_range = scaled.reduce(ee.Reducer.min()).gt(S2_MIN).And(scaled.reduce(ee.Reducer.max()).lte(S2_MAX))
    return image.addBands(scaled).updateMask(in_range)


def scl_mask(image):
    """Mask of the SCL classes that are never usable; the floor under every cloud filter."""
    rejected = image.select("SCL").remap(list(SCL_REJECTED), [1] * len(SCL_REJECTED), 0)
    return rejected.Not()


def _grow_rejection(keep_mask, image, radius_meters):
    """Take a keep mask and return it with its rejected area grown by a radius.

    Cloud and shadow edges are systematically under-detected, and their rim pixels are a common
    source of false breaks, so the rejected side is what gets buffered.

    Pinned to the 20 m SCL grid: without an explicit reproject a focal operation runs at whatever
    scale the consumer requests, so the same collection would be masked differently depending on
    how it is sampled.
    """
    return (
        keep_mask.Not()
        .focal_max(radius_meters, "circle", "meters")
        .reproject(crs=image.select("SCL").projection(), scale=SCL_SCALE)
        .Not()
    )


def _link_quality_band(collection, quality_name, point, date_range, doy_range, band, property_name):
    """Attach a per-scene quality image, keeping scenes that have no match.

    ee.Join.saveFirst defaults to an inner join, which silently drops every scene whose quality
    image is missing - the series just ends early with no warning. An outer join keeps the scene
    and leaves the property null so the caller can fall back.
    """
    import ee

    quality = filter_collection(quality_name, point, date_range, doy_range).select(band)
    return ee.ImageCollection(
        ee.Join.saveFirst(matchKey=property_name, outer=True).apply(
            primary=collection,
            secondary=quality,
            condition=ee.Filter.equals(leftField="system:index", rightField="system:index"),
        )
    )


def apply_cloud_score_plus(collection, point, date_range, doy_range):
    """Mask with Cloud Score+, which scores cloud, cirrus, haze and shadow in a single band."""
    import ee

    linked = _link_quality_band(collection, CLOUD_SCORE_PLUS, point, date_range, doy_range, CS_PLUS_BAND, "cloud_score")

    def mask_image(image):
        image = ee.Image(image)
        score = image.get("cloud_score")
        # scenes outside the Cloud Score+ archive keep only the SCL floor
        clear = ee.Image(
            ee.Algorithms.If(
                score,
                ee.Image(score).select(CS_PLUS_BAND).gte(CS_PLUS_THRESHOLD),
                ee.Image.constant(1),
            )
        )
        return image.updateMask(clear.And(scl_mask(image)))

    return linked.map(mask_image)


def apply_s2cloudless(collection, point, date_range, doy_range):
    """Mask with s2cloudless probabilities plus a solar-geometry cloud-shadow projection."""
    import ee

    linked = _link_quality_band(
        collection, S2_CLOUD_PROBABILITY, point, date_range, doy_range, "probability", "cloud_probability"
    )

    def mask_image(image):
        image = ee.Image(image)
        probability = image.get("cloud_probability")
        # a missing probability scene must not be read as "no cloud everywhere", so fall back to
        # the SCL cloud classes, which are always present
        is_cloud = ee.Image(
            ee.Algorithms.If(
                probability,
                ee.Image(probability).select("probability").gt(CLOUD_PROBABILITY_THRESHOLD),
                image.select("SCL").gte(8),
            )
        ).rename("clouds")

        # cloud shadows are dark NIR pixels lying where the sun projects the cloud
        not_water = image.select("SCL").neq(6)
        dark = image.select("NIR").lt(DARK_NIR_THRESHOLD).And(not_water)
        shadow_azimuth = ee.Number(90).subtract(ee.Number(image.get("MEAN_SOLAR_AZIMUTH_ANGLE")))
        projected = (
            is_cloud.directionalDistanceTransform(shadow_azimuth, CLOUD_PROJECTION_DISTANCE_KM * 10)
            .reproject(crs=image.select("SCL").projection(), scale=100)
            .select("distance")
            .mask()
        )
        cloud_or_shadow = is_cloud.Or(projected.And(dark))
        clear = _grow_rejection(cloud_or_shadow.Not(), image, CLOUD_BUFFER_METERS)
        return image.updateMask(clear.And(scl_mask(image)))

    return linked.map(mask_image)


def apply_sen2cor(collection):
    """Mask with the Sen2Cor scene classification, dilated to cover under-detected cloud edges."""
    return collection.map(lambda image: image.updateMask(_grow_rejection(scl_mask(image), image, CLOUD_BUFFER_METERS)))


def get_gee_data_sentinel(coords, date_range, doy_range, name, cloud_filter=DEFAULT_CLOUD_FILTER, indices=INDEX_BANDS):
    """Filtered, masked and index-augmented Sentinel-2 L2A series at a point.

    `indices` limits which spectral indices are computed; see gee_common.add_indices.
    """
    import ee

    point = ee.Geometry.Point(coords)
    collection_name = S2_SR if name == "Sentinel-2" else name

    # No scene-level CLOUDY_PIXEL_PERCENTAGE filter: this is a point analysis, so a scene that is
    # clear at the point must be kept however cloudy the rest of the tile is.
    collection = filter_collection(collection_name, point, date_range, doy_range).map(
        lambda image: add_indices(prepare_bands(image), TC_S2, indices)
    )

    if cloud_filter == "Cloud Score+":
        collection = apply_cloud_score_plus(collection, point, date_range, doy_range)
    elif cloud_filter == "s2cloudless":
        collection = apply_s2cloudless(collection, point, date_range, doy_range)
    elif cloud_filter == "Sen2Cor":
        collection = apply_sen2cor(collection)
    elif cloud_filter != "No Mask":
        raise ValueError(f"Unknown cloud filter: {cloud_filter}. Use one of {', '.join(CLOUD_FILTERS)}.")

    # Drop SCL and the raw L1C/L2A bands: CCDC fits coefficients for every band it is handed, and
    # the raw DN bands would be fitted at 10000x the scale the lambda is tuned for.
    # CCDC and the plot both need the series in chronological order.
    schema = [*OPTICAL_BANDS, *resolve_indices(indices)]
    return collection.select(schema).sort("system:time_start")
