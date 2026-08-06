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

from .gee_common import OPTICAL_BANDS, resolve_indices
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

# When each catalog starts, so an empty result can say whether the range was ever going to match.
# Sentinel-2 is the one that catches people out: the plugin's date range starts in 2000 by default,
# decades before Sentinel-2 existed, so any range narrowed to end early returns nothing at all.
DATASET_AVAILABILITY: Final = {
    "Landsat C2": ("1982-08-22", "Dense coverage from 1984."),
    "Sentinel-2": ("2017-03-28", "Global only from late 2018; use Landsat C2 for earlier years."),
}

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


def resolve_computed_indices(breakpoint_bands, plot_band=None, tmask_bands=None):
    """Which spectral indices this run has to build.

    The ones driving change detection, plus whatever is being plotted so the series and the fitted
    coefficients exist for it. Everything else is skipped - see gee_common.add_indices.
    """
    ccd_bands, _ = resolve_ccd_bands(breakpoint_bands, tmask_bands)
    return resolve_indices([*ccd_bands, *([plot_band] if plot_band else [])])


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
    never disagree with what was actually computed. Which indices were built is deliberately not
    part of the key: it travels with the value so a run that built more than a later view needs
    can still serve it - see lookup_result.
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


def _no_images_message(dataset, date_range):
    """Why the collection came back empty, in terms the user can act on."""
    start, note = DATASET_AVAILABILITY.get(dataset, (None, ""))
    # dates are ISO strings, so a plain comparison orders them correctly
    if start and date_range[1] <= start:
        return f"{dataset} has no data before {start}. {note}"
    return "No images at this point for the selected date and DOY range."


def lookup_result(key, indices):
    """A cached result for this key that already carries every index needed, or None.

    Only the indices a run needed were built, so an entry is reusable when its set is a superset
    of what the caller wants. That is what keeps switching the plotted band instant: a run built
    for NDVI also serves every optical band, since those are the scaled source and always present.
    """
    entry = ccd_results.get(key)
    if entry is None:
        return None
    built, ccdc_info, timeseries = entry
    if not set(indices) <= set(built):
        return None
    ccd_results.move_to_end(key)
    return ccdc_info, timeseries


def _store_result(key, indices, value):
    """Record a result with the index set it was built from.

    Keeps whichever run for this key covers more indices, and evicts the least recently used
    entry once the cache is over its bound.
    """
    existing = ccd_results.get(key)
    # A run that built more indices answers strictly more views, so never replace one with a
    # narrower run for the same key - every other parameter is already in the key.
    if existing is not None and set(indices) < set(existing[0]):
        ccd_results.move_to_end(key)
        return
    ccd_results[key] = (tuple(indices), *value)
    ccd_results.move_to_end(key)
    while len(ccd_results) > CACHE_MAX_ENTRIES:
        ccd_results.popitem(last=False)


def _build_timeseries(region_rows):
    """Turn a getRegion result into a column dictionary, rejecting fully masked points."""
    if len(region_rows) < 2:
        raise CCDComputationError("No observations at this point. Try a wider date or DOY range.")

    header = list(region_rows[0])
    # getRegion returns a row for every scene, including ones where the pixel is fully masked
    # (all values None), so a non-empty result does not by itself mean there is anything to fit.
    optical_columns = [header.index(band) for band in OPTICAL_BANDS if band in header]
    if not any(row[column] is not None for row in region_rows[1:] for column in optical_columns):
        raise CCDComputationError(
            "Every observation here is masked (cloud/shadow/snow). "
            "Try a wider date/DOY range or a less strict cloud filter."
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
    plot_band=None,
):
    # documentation: https://developers.google.com/earth-engine/apidocs/ee-algorithms-temporalsegmentation-ccdc
    import ee

    point = ee.Geometry.Point(coords)
    ccd_bands, tmask_bands = resolve_ccd_bands(breakpoint_bands, tmask_bands)
    indices = resolve_computed_indices(breakpoint_bands, plot_band, tmask_bands)

    if dataset == "Sentinel-2":
        gee_data = get_gee_data_sentinel(coords, date_range, doy_range, dataset, cloud_filter, indices)
    elif dataset == "Landsat C2":
        gee_data = get_gee_data_landsat(coords, date_range, doy_range, indices)
    else:
        raise CCDComputationError(f"Unsupported dataset: {dataset}. Use 'Landsat C2' or 'Sentinel-2'.")

    # One round trip that both proves the collection is non-empty and fetches the grid to sample
    # on. Both are needed before the parallel calls below, and asking for them together keeps it
    # to a single serial request.
    # One serial round trip for the two things the parallel requests below both need: proof the
    # collection is non-empty, and the grid to sample on. Reusing `first` for the projection keeps
    # this to a single size() evaluation rather than the two the If condition used to force.
    first = gee_data.first()
    catalog = ee.Dictionary(
        {
            "size": gee_data.size(),
            "projection": ee.Algorithms.If(first, ee.Image(first).select(0).projection(), ee.Projection("EPSG:4326")),
        }
    ).getInfo()
    if not catalog["size"]:
        raise CCDComputationError(_no_images_message(dataset, date_range))
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
        indices,
        (ccdc_info, timeseries),
    )

    return ccdc_info, timeseries
