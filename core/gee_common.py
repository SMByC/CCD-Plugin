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

# EVI/EVI2 are ratios whose denominator can approach zero on bright hazy or cloud-edge pixels,
# which yields values orders of magnitude outside the physical range. A single such observation
# dominates the LASSO fit of a whole CCDC segment and rescales the plot, so clamp to the
# physically meaningful interval instead of letting the outlier through.
# (NDVI/NBR need no clamp: ee.Image.normalizedDifference already masks negative inputs and a
# zero denominator, so it cannot produce out-of-range values.)
INDEX_RANGE: Final = (-1.0, 1.0)

# A full-year window means "no seasonal restriction", not "days 1 to 365": the GUI reports it
# whenever the user has not narrowed the season, and it is what the DOY controls fall back to when
# they are disabled. Every scene in the date range is wanted, so no day-of-year filter is built.
FULL_YEAR: Final = (1, 365)


def date_and_doy_filter(date_range, doy_range):
    """Filter for the date range, narrowed to a day-of-year window when one was asked for.

    A DOY window such as 300-60 (southern-hemisphere dry season) is not expressible as a single
    ee.Filter.dayOfYear call: the naive form would ask for start <= doy <= end with start > end
    and match nothing, silently returning an empty collection.
    """
    import ee

    start_doy, end_doy = doy_range
    date_filter = ee.Filter.date(ee.Date(date_range[0]), ee.Date(date_range[1]))
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
    near_infrared, red, blue = image.select("NIR"), image.select("Red"), image.select("Blue")
    low, high = INDEX_RANGE

    # ee.Reducer.sum() skips masked bands rather than propagating the mask, so a pixel with one
    # band missing would come back as a silently short weighted sum instead of masked - which is
    # what ee.Image.expression did. Re-apply "every input band valid" to restore that.
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
        "NDVI": lambda: image.normalizedDifference(["NIR", "Red"]).rename("NDVI"),
        "NBR": lambda: image.normalizedDifference(["NIR", "SWIR2"]).rename("NBR"),
        # 2.5 * (NIR - Red) / (NIR + 6 * Red - 7.5 * Blue + 1)
        "EVI": lambda: (
            near_infrared.subtract(red)
            .multiply(2.5)
            .divide(near_infrared.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1))
            .rename("EVI")
            .clamp(low, high)
        ),
        # 2.5 * (NIR - Red) / (NIR + 2.4 * Red + 1)
        "EVI2": lambda: (
            near_infrared.subtract(red)
            .multiply(2.5)
            .divide(near_infrared.add(red.multiply(2.4)).add(1))
            .rename("EVI2")
            .clamp(low, high)
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
