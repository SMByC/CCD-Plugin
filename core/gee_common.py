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


def date_and_doy_filter(date_range, doy_range):
    """Filter for the date range plus a day-of-year window, supporting windows that wrap the year.

    A DOY window such as 300-60 (southern-hemisphere dry season) is not expressible as a single
    ee.Filter.dayOfYear call: the naive form would ask for start <= doy <= end with start > end
    and match nothing, silently returning an empty collection.
    """
    import ee

    start_doy, end_doy = doy_range
    date_filter = ee.Filter.date(ee.Date(date_range[0]), ee.Date(date_range[1]))
    if start_doy <= end_doy:
        doy_filter = ee.Filter.dayOfYear(start_doy, end_doy)
    else:
        doy_filter = ee.Filter.Or(ee.Filter.dayOfYear(start_doy, 366), ee.Filter.dayOfYear(1, end_doy))
    return ee.Filter.And(date_filter, doy_filter)


def filter_collection(collection_name, point, date_range, doy_range):
    """Collection restricted to the images covering the point inside the date and DOY window."""
    import ee

    return ee.ImageCollection(collection_name).filterBounds(point).filter(date_and_doy_filter(date_range, doy_range))


def tc_expression(coefficients):
    """Tasseled cap expression string from coefficients ordered as OPTICAL_BANDS."""
    return " + ".join(f"({coef}) * {band}" for coef, band in zip(coefficients, OPTICAL_BANDS, strict=True))


def add_indices(image, tc_coefficients):
    """Add NDVI, NBR, EVI, EVI2 and the sensor-specific tasseled cap indices to a scaled image.

    `image` must already expose the OPTICAL_BANDS schema in surface reflectance units (0-1).
    """
    bands = {band: image.select(band) for band in OPTICAL_BANDS}
    low, high = INDEX_RANGE
    indices = [
        image.normalizedDifference(["NIR", "Red"]).rename("NDVI"),
        image.normalizedDifference(["NIR", "SWIR2"]).rename("NBR"),
        image.expression("2.5 * ((NIR - Red) / (NIR + 6 * Red - 7.5 * Blue + 1))", bands)
        .rename("EVI")
        .clamp(low, high),
        image.expression("2.5 * ((NIR - Red) / (NIR + 2.4 * Red + 1))", bands).rename("EVI2").clamp(low, high),
        image.expression(tc_expression(tc_coefficients["BRIGHTNESS"]), bands).rename("BRIGHTNESS").toFloat(),
        image.expression(tc_expression(tc_coefficients["GREENNESS"]), bands).rename("GREENNESS").toFloat(),
        image.expression(tc_expression(tc_coefficients["WETNESS"]), bands).rename("WETNESS").toFloat(),
    ]
    return image.addBands(indices)
