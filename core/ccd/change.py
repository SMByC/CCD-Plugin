"""
Methods used by the change detection procedures. There should be no default 
values for input arguments, as all values should be supplied by the calling
method.

These should be as close to the functional paradigm as possible.
"""
import logging
import numpy as np
from scipy.stats import chi2

from ccd.models import lasso
from ccd.math_utils import sum_of_squares

log = logging.getLogger(__name__)


def stable(models, dates, variogram, t_cg, detection_bands):
    """Determine if we have a stable model to start building with

    Args:
        models: list of current representative/fitted models
        variogram: 1-d array of variogram values to compare against for the
            normalization factor
        dates: array of ordinal date values
        t_cg: change threshold
        detection_bands: index locations of the spectral bands that are used
            to determine stability

    Returns:
        Boolean on whether stable or not
    """
    # This could be written differently, or more performant using numpy in the
    # future
    check_vals = []
    for idx in detection_bands:
        rmse_norm = max(variogram[idx], models[idx].rmse)
        slope = models[idx].fitted_model.coef_[0] * (dates[-1] - dates[0])

        check_val = (abs(slope) + abs(models[idx].residual[0]) +
                     abs(models[idx].residual[-1])) / rmse_norm

        check_vals.append(check_val)

    euc_norm = sum_of_squares(np.array(check_vals))
    log.debug('Stability norm: %s, Check against: %s', euc_norm, t_cg)

    return euc_norm < t_cg


def change_magnitude(residuals, variogram, comparison_rmse):
    """
    Calculate the magnitude of change for multiple points in time.

    Args:
        residuals: predicted - observed values across the desired bands,
            expecting a 2-d array with each band as a row and the observations
            as columns
        variogram: 1-d array of variogram values to compare against for the
            normalization factor
        comparison_rmse: values to compare against the variogram values

    Returns:
        1-d ndarray of values representing change magnitudes
    """
    rmse = np.maximum(variogram, comparison_rmse)

    magnitudes = residuals / rmse[:, None]

    change_mag = sum_of_squares(magnitudes, axis=0)

    log.debug('Magnitudes of change: %s', change_mag)

    return change_mag


def calc_residuals(dates, observations, model, avg_days_yr):
    """
    Calculate the residuals using the fitted model.

    Args:
        dates: ordinal dates associated with the observations
        observations: spectral observations
        model: named tuple with the scipy model, rmse, and residuals

    Returns:
        1-d ndarray of residuals
    """
    # This needs to be modularized in the future.
    # Basically the model object should have a predict method with it.
    return np.abs(observations - lasso.predict(model, dates, avg_days_yr))


def detect_change(magnitudes, change_threshold):
    """
    Convenience function to check if the minimum magnitude surpasses the
    threshold required to determine if it is change.

    Args:
        magnitudes: change magnitude values across the observations
        change_threshold: threshold value to determine if change has occurred

    Returns:
        bool: True if change has been detected, else False
    """
    return np.min(magnitudes) > change_threshold


def detect_outlier(magnitude, outlier_threshold):
    """
    Convenience function to check if any of the magnitudes surpass the
    threshold to mark this date as being an outlier

    This is used to mask out values from current or future processing

    Args:
        magnitude: float, magnitude of change at a given moment in time
        outlier_threshold: threshold value

    Returns:
        bool: True if these spectral values should be omitted
    """
    return magnitude > outlier_threshold


def find_time_index(dates, window, meow_size, day_delta):
    """Find index in times at least one year from time at meow_ix.
    Args:
        dates: list of ordinal day numbers relative to some epoch,
            the particular epoch does not matter.
        window: index into times, used to get day number for comparing
            times
        meow_size: minimum expected observation window needed to
            produce a fit.
        day_delta: number of days required for a years worth of data,
            defined to be 365
    Returns:
        integer: array index of time at least one year from meow_ix,
            or None if it can't be found.
    """

    # If the last time is less than a year, then iterating through
    # times to find an index is futile.
    if not enough_time(dates, day_delta=day_delta):
        log.debug('Insufficient time: %s', dates[-1] - dates[0])
        return None

    if window.stop:
        end_ix = window.stop
    else:
        end_ix = window.start + meow_size

    # This seems pretty naive, if you can think of something more
    # performant and elegant, have at it!
    while end_ix < dates.shape[0] - meow_size:
        if (dates[end_ix]-dates[window.start]) >= day_delta:
            break
        else:
            end_ix += 1

    log.debug('Sufficient time from times[{0}..{1}] (day #{2} to #{3})'
              .format(window.start, end_ix, dates[window.start], dates[end_ix]))

    return end_ix


def enough_samples(dates, meow_size):
    """Change detection requires a minimum number of samples (as specified
    by meow size).

    This function improves readability of logic that performs this check.

    Args:
        dates: list of ordinal day numbers relative to some epoch,
            the particular epoch does not matter.
        meow_size: minimum expected observation window needed to
            produce a fit.

    Returns:
        bool: True if times contains enough samples
        False otherwise.
    """
    return len(dates) >= meow_size


def enough_time(dates, day_delta):
    """Change detection requires a minimum amount of time (as specified by
    day_delta).

    This function, like `enough_samples` improves readability of logic
    that performs this check.

    Args:
        dates: list of ordinal day numbers relative to some epoch,
            the particular epoch does not matter.
        day_delta: minimum difference between time at meow_ix and most
            recent observation.

    Returns:
        bool: True if the represented time span is greater than day_delta
    """
    return (dates[-1] - dates[0]) >= day_delta


def determine_num_coefs(dates, min_coef, mid_coef, max_coef, num_obs_factor):
    """
    Determine the number of coefficients to use for the main fit procedure

    This is based mostly on the amount of time (in ordinal days) that is
    going to be covered by the model

    This is referred to as df (degrees of freedom) in the model section

    Args:
        dates: 1-d array of representative ordinal dates
        min_coef: minimum number of coefficients
        mid_coef: mid number of coefficients
        max_coef: maximum number of coefficients
        num_obs_factor: used to scale the time span

    Returns:
        int: number of coefficients to use during the fitting process
    """
    span = dates.shape[0] / num_obs_factor

    if span < mid_coef:
        return min_coef
    elif span < max_coef:
        return mid_coef
    else:
        return max_coef


def update_processing_mask(mask, index, window=None):
    """
    Update the persistent processing mask.

    Because processes apply the mask first, index values given are in relation
    to that. So we must apply the mask to itself, then update the boolean
    values.

    The window slice object is to catch when it is in relation to some
    window of the masked values. So, we must mask against itself, then look at
    a subset of that result.

    This method should create a new view object to avoid mutability issues.

    Args:
        mask: 1-d boolean ndarray, current mask being used
        index: int/list/tuple of index(es) to be excluded from processing,
            or boolean array
        window: slice object identifying a further subset of the mask

    Returns:
        1-d boolean ndarray
    """
    new_mask = mask[:]
    sub_mask = new_mask[new_mask]

    if window:
        sub_mask[window][index] = False
    else:
        sub_mask[index] = False

    new_mask[new_mask] = sub_mask

    return new_mask


def find_closest_doy(dates, date_idx, window, num):
    """
    Find the closest n dates based on day of year.

    e.g. if the date you are looking for falls on July 1, then find
    n number of dates that are closest to that same day of year.

    Args:
        dates: 1-d ndarray of ordinal day values
        date_idx: index of date value
        window: slice object identifying the subset of values used in the
            current model
        num: number of index values desired

    Returns:
        1-d ndarray of index values
    """
    # May be a better way of doing this
    d_rt = dates[window] - dates[date_idx]
    d_yr = np.abs(np.round(d_rt / 365.25) * 365.25 - d_rt)

    return np.argsort(d_yr)[:num]


def adjustpeek(dates, defpeek):
    delta = np.median(np.diff(dates))
    adj_peek = int(np.round(defpeek * 16 / delta))

    return adj_peek if adj_peek > defpeek else defpeek


def adjustchgthresh(peek, defpeek, defthresh):
    thresh = defthresh
    if peek > defpeek:
        pt_cg = 1 - (1 - 0.99) ** (defpeek / peek)
        thresh = chi2.ppf(pt_cg, 5)

    return thresh
