"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019-2026 by Xavier Corredor Llano, SMByC
        email                : xavier.corredor.llano@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/

Pieces shared by the Landsat and Sentinel-2 collection builders: the common band
schema, the date/day-of-year filter and the spectral indices.
"""

from typing import Final

OPTICAL_BANDS: Final = ("Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2")
INDEX_BANDS: Final = ("NDVI", "NBR", "EVI", "EVI2", "BRIGHTNESS", "GREENNESS", "WETNESS")
# Schema every dataset must expose so that CCDC, the cache key and the plot are dataset-agnostic
CCD_BANDS: Final = (*OPTICAL_BANDS, *INDEX_BANDS)
# The optical bands each index is computed from, to tell when a change-detection set repeats itself
INDEX_SOURCES: Final = {
    "NDVI": ("NIR", "Red"),
    "NBR": ("NIR", "SWIR2"),
    "EVI": ("NIR", "Red", "Blue"),
    "EVI2": ("NIR", "Red"),
    "BRIGHTNESS": OPTICAL_BANDS,
    "GREENNESS": OPTICAL_BANDS,
    "WETNESS": OPTICAL_BANDS,
}

# An observation is used only when all six optical bands are physical surface reflectance,
# 0 < SR <= 1, whatever the dataset. This is the rule of the reference CCDC implementations:
# Zhu's CCDC code (v12.30) keeps 0 < SR < 10000 on all six bands, LCMAP pyccd does the same in
# qa.filter_saturated, and gee-ccdc-tools (Arevalo et al. 2020) masks any band <= 0. USGS
# describes negative Landsat SR as a known computational artefact of the atmospheric correction
# over dark targets: an over-estimated aerosol pushes the short wavelengths below zero and biases
# the rest of that observation low too. CCDC is built for irregular sampling, so a dropped
# observation costs little, while a biased one ends up in the residuals the change test reads.
# Measured over 95 stratified points in Colombia (2000-2026) the rule removes ~1% of clear Landsat
# observations, nearly all of them over water. Above 1 is residual cloud, snow or saturation.
REFLECTANCE_RANGE: Final = (0.0, 1.0)

# EVI/EVI2 are ratios whose denominator can approach zero on bright hazy or cloud-edge pixels,
# which yields values orders of magnitude outside the physical range. A single such observation
# dominates the LASSO fit of a whole CCDC segment and rescales the plot, so clamp to the
# physically meaningful interval instead of letting the outlier through.
INDEX_RANGE: Final = (-1.0, 1.0)

# A full-year window means "no seasonal restriction", not "days 1 to 365": the GUI reports it
# whenever the user has not narrowed the season, and it is what the DOY controls fall back to when
# they are disabled. Every scene in the date range is wanted, so no day-of-year filter is built.
FULL_YEAR: Final = (1, 365)


def valid_reflectance(scaled):
    """Mask of the pixels whose optical bands are all physical surface reflectance.

    `scaled` holds the OPTICAL_BANDS schema in reflectance units; see REFLECTANCE_RANGE.
    """
    import ee

    low, high = REFLECTANCE_RANGE
    return scaled.reduce(ee.Reducer.min()).gt(low).And(scaled.reduce(ee.Reducer.max()).lte(high))


def date_and_doy_filter(date_range, doy_range):
    """Filter for the date range, narrowed to a day-of-year window when one was asked for.

    Both dates are inclusive, as the date controls present them: Earth Engine's date filter
    excludes its end, so the end is moved to the start of the following day.

    A DOY window such as 300-60 (southern-hemisphere dry season) is not expressible as a single
    ee.Filter.dayOfYear call: the naive form would ask for start <= doy <= end with start > end
    and match nothing, silently returning an empty collection.
    """
    import ee

    start_doy, end_doy = doy_range
    date_filter = ee.Filter.date(ee.Date(date_range[0]), ee.Date(date_range[1]).advance(1, "day"))
    if (start_doy, end_doy) == FULL_YEAR:
        # No season was chosen, so the date range alone is the selection. This keeps 31 December
        # of a leap year (DOY 366) too, which an explicit dayOfYear(1, 365) would have dropped.
        return date_filter
    if start_doy <= end_doy:
        doy_filter = ee.Filter.dayOfYear(start_doy, end_doy)
    else:
        doy_filter = ee.Filter.Or(ee.Filter.dayOfYear(start_doy, 366), ee.Filter.dayOfYear(1, end_doy))
    return ee.Filter.And(date_filter, doy_filter)


def filter_collection(collection_name, point, date_range, doy_range):
    """Collection restricted to the images covering the point inside the date and DOY window."""
    import ee

    return ee.ImageCollection(collection_name).filterBounds(point).filter(date_and_doy_filter(date_range, doy_range))


def add_indices(image, tc_coefficients, indices=INDEX_BANDS):
    """Add the requested spectral indices to a scaled image, in the canonical INDEX_BANDS order.

    `image` must already expose the OPTICAL_BANDS schema in surface reflectance units (0-1).

    `indices` is the subset actually needed - the breakpoint bands plus whatever is being plotted.
    Computing all seven regardless roughly doubles the retrieval leg of a point query, and in the
    default configuration (change detection on the optical bands, SWIR1 plotted) not one of them
    is used. The optical bands are the scaled source, so they always come along free.

    Written as band arithmetic rather than ee.Image.expression. The two produce identical values
    (verified against a live series: tasseled cap and the normalised differences bit for bit, EVI
    and EVI2 within 1e-16), but an expression is a string Earth Engine has to parse into a graph
    for every scene, and a point query over a 40-year series is thousands of scenes. Measured over
    six fresh points, dropping the expressions took the median retrieval from 40.9s to 26.1s.
    """
    import ee

    wanted = resolve_indices(indices)
    if not wanted:
        return image

    optical = image.select(list(OPTICAL_BANDS))
    low, high = INDEX_RANGE

    # Every derived band has to be defined wherever the optical bands are. CCDC drops a whole
    # observation as soon as any of its bands is masked, so an index that masks a pixel the optical
    # bands keep removes that observation from the fit of every band: plotting NDVI used to change
    # the segments drawn for SWIR1 (measured: 136 observations fitted at a lake, 122 with NDVI
    # plotted). ee.Image.normalizedDifference is such an index, it masks negative inputs and a zero
    # denominator. The ratios here are computed from inputs floored at 0 instead: with the inputs
    # inside REFLECTANCE_RANGE the floor is a no-op, and it keeps the ratios defined (Earth Engine
    # returns 0 for 0/0) should the validity rule ever be relaxed.
    #
    # Every derived band is also cast to plain float: CCDC requires a homogeneous collection, and
    # the value range Earth Engine infers for an arithmetic result differs between sensors.
    near_infrared, red, blue, swir2 = (image.select(band).max(0) for band in ("NIR", "Red", "Blue", "SWIR2"))

    def normalized_difference(first, second, name):
        # (a - b) / (a + b) of non-negative inputs is already within [-1, 1]
        return first.subtract(second).divide(first.add(second)).rename(name).toFloat()

    # ee.Reducer.sum() skips masked bands rather than propagating the mask, so a pixel with one
    # band missing would come back as a silently short weighted sum instead of masked - which is
    # what ee.Image.expression did. Re-apply "every input band valid" to restore that. It is the
    # optical bands' own mask, so it masks nothing they do not.
    all_bands_valid = optical.mask().reduce(ee.Reducer.min())

    def tasseled_cap(component):
        # weighted sum over the optical stack, the same thing the expression spelled out term by term
        return (
            optical.multiply(tc_coefficients[component])
            .reduce(ee.Reducer.sum())
            .updateMask(all_bands_valid)
            .rename(component)
            .toFloat()
        )

    builders = {
        "NDVI": lambda: normalized_difference(near_infrared, red, "NDVI"),
        "NBR": lambda: normalized_difference(near_infrared, swir2, "NBR"),
        # 2.5 * (NIR - Red) / (NIR + 6 * Red - 7.5 * Blue + 1)
        "EVI": lambda: (
            near_infrared.subtract(red)
            .multiply(2.5)
            .divide(near_infrared.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1))
            .rename("EVI")
            .clamp(low, high)
            .toFloat()
        ),
        # 2.5 * (NIR - Red) / (NIR + 2.4 * Red + 1)
        "EVI2": lambda: (
            near_infrared.subtract(red)
            .multiply(2.5)
            .divide(near_infrared.add(red.multiply(2.4)).add(1))
            .rename("EVI2")
            .clamp(low, high)
            .toFloat()
        ),
        "BRIGHTNESS": lambda: tasseled_cap("BRIGHTNESS"),
        "GREENNESS": lambda: tasseled_cap("GREENNESS"),
        "WETNESS": lambda: tasseled_cap("WETNESS"),
    }
    return image.addBands([builders[name]() for name in wanted])


def resolve_indices(bands):
    """The index half of a requested band set, in canonical order.

    The optical bands need no resolving: they are the scaled source and are always present.
    """
    requested = set(bands)
    return tuple(name for name in INDEX_BANDS if name in requested)
