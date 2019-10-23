"""
Contains commonly used math functions.

This file is meant to help code reuse, profiling, and look at speeding up
individual operations.

In the interest of avoiding circular imports, this should be kept to be fairly
stand-alone. I.e. it should not import any other piece of the overall project.
"""
from functools import wraps

import numpy as np
from scipy.stats import mode

# TODO: Cache timings
# TODO: Numba timings


def adjusted_variogram(dates, observations):
    """
    Calculate a modified first order variogram/madogram.

    This method differentiates from the standard calculate_variogram in that
    it attempts to only use observations that are greater than 30 days apart.

    This attempts to combat commission error due to temporal autocorrelation.

    Args:
        dates: 1-d array of values representing ordinal day
        observations: 2-d array of spectral observations corresponding to the
            dates array

    Returns:
        1-d ndarray of floats
    """
    vario = calculate_variogram(observations)

    for idx in range(dates.shape[0]):
        var = dates[1 + idx:] - dates[:-idx - 1]

        majority = mode(var)[0][0]

        if majority > 30:
            diff = observations[:, 1 + idx:] - observations[:, :-idx - 1]
            ids = var > 30

            vario = np.median(np.abs(diff[:, ids]), axis=1)
            break

    return vario


def euclidean_norm(vector):
    """
    Calculate the euclidean norm across a vector

    This is the default norm method used by Matlab

    Args:
        vector: 1-d array of values

    Returns:
        float
    """
    return np.sum(vector ** 2) ** .5


def sum_of_squares(vector, axis=None):
    """
    Squares the values, then adds them up
    
    Args:
        vector: 1-d array of values, or n-d array with an axis set
        axis: numpy axis to operate on in cases of more than 1-d array

    Returns:
        float
    """
    return np.sum(vector ** 2, axis=axis)


def calc_rmse(actual, predicted, num_pm=0):
    """
    Calculate the root mean square of error for the given inputs

    Args:
        actual: 1-d array of values, observed
        predicted: 1-d array of values, predicted
        num_pm: number of parameters to use for the calculation if based on a
            smaller sample set

    Returns:
        float: root mean square value
        1-d ndarray: residuals
    """
    residuals = calc_residuals(actual, predicted)

    return ((np.sum(residuals ** 2) / (residuals.shape[0] - num_pm)) ** 0.5,
            residuals)


def calc_median(vector):
    """
    Calculate the median value of the given vector

    Args:
        vector: array of values

    Returns:
        float: median value
    """
    return np.median(vector)


def calc_residuals(actual, predicted):
    """
    Helper method to make other code portions clearer

    Args:
        actual: 1-d array of observed values
        predicted: 1-d array of predicted values

    Returns:
        ndarray: 1-d array of residual values
    """
    return actual - predicted


def kelvin_to_celsius(thermals, scale=10):
    """
    Convert kelvin values to celsius

    L2 processing for the thermal band (known as Brightness Temperature) is
    initially done in kelvin and has been scaled by a factor of 10 already,
    in the interest of keeping the values in integer space, a further factor
    of 10 is calculated.

    scaled C = K * 10 - 27315
    unscaled C = K / 10 - 273.15

    Args:
        thermals: 1-d ndarray of scaled thermal values in kelvin
        scale: int scale factor used for the thermal values

    Returns:
        1-d ndarray of thermal values in scaled degrees celsius
    """
    return thermals * scale - 27315


def calculate_variogram(observations):
    """
    Calculate the first order variogram/madogram across all bands

    Helper method to make subsequent code clearer

    Args:
        observations: spectral band values

    Returns:
        1-d ndarray representing the variogram values
    """
    return np.median(np.abs(np.diff(observations)), axis=1)


def mask_duplicate_values(vector):
    """
    Mask out duplicate values.

    Mainly used for removing duplicate observation dates from the dataset.
    Just because there are duplicate observation dates, doesn't mean that 
    both have valid data.

    Generally this should be applied after other masks.

    Arg:
        vector: 1-d ndarray, ordinal date values

    Returns:
        1-d boolean ndarray
    """
    mask = np.zeros_like(vector, dtype=np.bool)
    mask[np.unique(vector, return_index=True)[1]] = 1

    return mask


def mask_value(vector, val):
    """
    Build a boolean mask around a certain value in the vector.
    
    Args:
        vector: 1-d ndarray of values
        val: values to mask on

    Returns:
        1-d boolean ndarray
    """
    return vector == val


def count_value(vector, val):
    """
    Count the number of occurrences of a value in the vector.
    
    Args:
        vector: 1-d ndarray of values
        val: value to count

    Returns:
        int
    """
    return np.sum(mask_value(vector, val))
