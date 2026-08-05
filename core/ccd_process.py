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

with the collaboration of Daniel Moraes <moraesd90@gmail.com>

"""

import concurrent.futures
from collections import OrderedDict
from typing import Final

import numpy as np

from .gee_common import OPTICAL_BANDS
from .gee_data_landsat import get_gee_data_landsat
from .gee_data_sentinel import DEFAULT_CLOUD_FILTER, get_gee_data_sentinel

# TMask is CCDC's iterative temporal cloud screen: it removes residual clouds and shadows that got
# past the per-image QA masks, which is what lets those masks stay permissive. Green and SWIR1 are
# the pair it separates cloud (bright in green) from shadow and snow (dark in SWIR) with - the
# Earth Engine signature calls them "typically the green band and the SWIR1 band", and both
# Zhu & Woodcock (2014) and the gee-ccdc-tools toolkit use that pair.
DEFAULT_TMASK_BANDS: Final = ("Green", "SWIR1")
# Change detection runs on every band but Blue. Blue carries the most residual haze and aerosol
# signal, so including it inflates the joint chi-square statistic with atmospheric noise and is a
# well documented source of false breaks. Zhu & Woodcock (2014) detect change on bands 2-5 and 7,
# the gee-ccdc-tools toolkit uses GREEN/RED/NIR/SWIR1/SWIR2, and SEPAL recommends "all color bands
# but blue". Both TMask bands are inside this set, so the default selection is never widened.
DEFAULT_BREAKPOINT_BANDS: Final = ("Green", "Red", "NIR", "SWIR1", "SWIR2")
# dateFormat=2 makes tStart/tEnd/tBreak unix milliseconds, matching the 'time' column of getRegion
CCDC_DATE_FORMAT: Final = 2

# Each entry holds the full coefficient set and the whole time series, so it is tens of MB for a
# multi-decade series; keep only enough to make band switching and small parameter tweaks instant.
CACHE_MAX_ENTRIES: Final = 8
ccd_results: "OrderedDict[tuple, tuple]" = OrderedDict()


class CCDComputationError(Exception):
    """A CCD run that cannot produce a plot: bad inputs, or no usable observations at the point.

    Carries a message written for the user, since the GUI shows it verbatim in the message bar.
    """


def resolve_ccd_bands(breakpoint_bands, tmask_bands=None):
    """Bands to hand CCDC, and the TMask bands, as the Earth Engine algorithm requires them.

    Ccdc rejects any call where the TMask bands are not also breakpoint bands
    ("The breakpointBands must include all of tmaskBands"), so they are unioned in. Callers should
    tell the user when this widened their selection, because it widens change detection too.
    """
    tmask = tuple(tmask_bands) if tmask_bands else DEFAULT_TMASK_BANDS
    return tuple(dict.fromkeys([*breakpoint_bands, *tmask])), tmask


def make_cache_key(
    coords,
    date_range,
    doy_range,
    dataset,
    breakpoint_bands,
    num_obs,
    chi_square,
    min_years,
    lambda_lasso,
    tmask_bands=None,
    cloud_filter=DEFAULT_CLOUD_FILTER,
):
    """Cache key for a CCD computation; includes every parameter that affects the result.

    It keys on the *effective* band set rather than the user's selection so that a lookup can
    never disagree with what was actually computed.
    """
    ccd_bands, tmask = resolve_ccd_bands(breakpoint_bands, tmask_bands)
    return (
        tuple(coords),
        tuple(date_range),
        tuple(doy_range),
        dataset,
        ccd_bands,
        tmask,
        num_obs,
        chi_square,
        min_years,
        lambda_lasso,
        cloud_filter,
    )


def _store_result(key, value):
    """Record a result, evicting the least recently used entry past the cache bound."""
    ccd_results[key] = value
    ccd_results.move_to_end(key)
    while len(ccd_results) > CACHE_MAX_ENTRIES:
        ccd_results.popitem(last=False)


def _build_timeseries(region_rows):
    """Turn a getRegion result into a column dictionary, rejecting fully masked points."""
    if len(region_rows) < 2:
        raise CCDComputationError(
            "No clear observations for this point with the current date range, DOY range "
            "and cloud mask. Try a wider date/DOY range or a less strict cloud filter."
        )

    header = list(region_rows[0])
    # getRegion returns a row for every scene, including ones where the pixel is fully masked
    # (all values None), so a non-empty result does not by itself mean there is anything to fit.
    optical_columns = [header.index(band) for band in OPTICAL_BANDS if band in header]
    if not any(row[column] is not None for row in region_rows[1:] for column in optical_columns):
        raise CCDComputationError(
            "All observations at this point are masked (cloud/shadow/snow) with the current "
            "date range, DOY range and cloud filter. Try a wider range or a less strict filter."
        )

    stacked = np.stack(region_rows[1:], axis=1)
    # keys are: id, longitude, latitude, time, Blue, Green, Red, ... NDVI, NBR, ...
    return {name: stacked[index] for index, name in enumerate(header)}


def compute_ccd(
    coords,
    date_range,
    doy_range,
    dataset,
    breakpoint_bands,
    tmask_bands,
    num_obs,
    chi_square,
    min_years,
    lambda_lasso,
    cloud_filter=DEFAULT_CLOUD_FILTER,
):
    # documentation: https://developers.google.com/earth-engine/apidocs/ee-algorithms-temporalsegmentation-ccdc
    import ee

    point = ee.Geometry.Point(coords)
    ccd_bands, tmask_bands = resolve_ccd_bands(breakpoint_bands, tmask_bands)

    if dataset == "Sentinel-2":
        gee_data = get_gee_data_sentinel(coords, date_range, doy_range, dataset, cloud_filter)
    elif dataset == "Landsat C2":
        gee_data = get_gee_data_landsat(coords, date_range, doy_range)
    else:
        raise CCDComputationError(f"Unsupported dataset: {dataset}. Use 'Landsat C2' or 'Sentinel-2'.")

    # One round trip that both proves the collection is non-empty and fetches the grid to sample
    # on. Both are needed before the parallel calls below, and asking for them together keeps it
    # to a single serial request.
    catalog = ee.Dictionary(
        {
            "size": gee_data.size(),
            "projection": ee.Algorithms.If(
                gee_data.size().gt(0), gee_data.first().select(0).projection(), ee.Projection("EPSG:4326")
            ),
        }
    ).getInfo()
    if not catalog["size"]:
        raise CCDComputationError(
            "No images available for this point with the current date and DOY range. Try a wider date or DOY range."
        )
    # Sample the observations and the CCDC fit on exactly the same pixels, and on the pixels the
    # source images actually have. Asking for a nominal `scale` makes Earth Engine derive a fresh
    # grid whose origin is not the source grid's: Landsat products are aligned to the 15 m
    # panchromatic lattice, so their 30 m origins are always odd multiples of 15 and a derived
    # grid lands half a pixel off in both axes. Measured on a Landsat series, `scale` alone and
    # `crs` + `scale` each returned a different pixel centre from the native grid and different
    # values on every shared date, by up to 0.016 reflectance - about the size of the segment RMSE
    # CCDC compares residuals against. Passing crs *and* crsTransform pins both calls to the
    # source grid, so the plotted observations and the fitted model come from the clicked pixel.
    projection = catalog["projection"]
    grid = {"crs": projection["crs"], "crsTransform": projection["transform"]}

    def get_time_series():
        rows = ee.List(gee_data.getRegion(geometry=point, **grid)).getInfo()
        return _build_timeseries(rows)

    def get_ccdc():
        # The whole collection is passed, not just the breakpoint bands: CCDC fits coefficients for
        # every band it is handed and the plot needs the coefficients of whichever band the user
        # selects, not only the ones driving detection.
        ccdc = ee.Algorithms.TemporalSegmentation.Ccdc(
            gee_data,
            list(ccd_bands),
            list(tmask_bands),
            num_obs,
            chi_square,
            min_years,
            CCDC_DATE_FORMAT,
            lambda_lasso,
        )
        return ccdc.reduceRegion(ee.Reducer.toList(), point, **grid).getInfo()

    # both are independent round trips to Earth Engine, so overlap them
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_timeseries = executor.submit(get_time_series)
        future_ccdc = executor.submit(get_ccdc)
        timeseries = future_timeseries.result()
        ccdc_info = future_ccdc.result()

    _store_result(
        make_cache_key(
            coords,
            date_range,
            doy_range,
            dataset,
            breakpoint_bands,
            num_obs=num_obs,
            chi_square=chi_square,
            min_years=min_years,
            lambda_lasso=lambda_lasso,
            tmask_bands=tmask_bands,
            cloud_filter=cloud_filter,
        ),
        (ccdc_info, timeseries),
    )

    return ccdc_info, timeseries
