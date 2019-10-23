"""Filters for pre-processing change model inputs.
"""
import numpy as np

from ccd.math_utils import calc_median, mask_value, count_value, mask_duplicate_values


def checkbit(packedint, offset):
    """
    Check for a bit flag in a given int value.
    
    Args:
        packedint: bit packed int
        offset: binary offset to check

    Returns:
        bool
    """
    bit = 1 << offset

    return (packedint & bit) > 0


def qabitval(packedint, proc_params):
    """
    Institute a hierarchy of qa values that may be flagged in the bitpacked
    value.
    
    fill > cloud > shadow > snow > water > clear
    
    Args:
        packedint: int value to bit check
        proc_params: dictionary of processing parameters

    Returns:
        offset value to use
    """
    if checkbit(packedint, proc_params.QA_FILL):
        return proc_params.QA_FILL
    elif checkbit(packedint, proc_params.QA_CLOUD):
        return proc_params.QA_CLOUD
    elif checkbit(packedint, proc_params.QA_SHADOW):
        return proc_params.QA_SHADOW
    elif checkbit(packedint, proc_params.QA_SNOW):
        return proc_params.QA_SNOW
    elif checkbit(packedint, proc_params.QA_WATER):
        return proc_params.QA_WATER
    elif checkbit(packedint, proc_params.QA_CLEAR):
        return proc_params.QA_CLEAR
    # L8 Cirrus and Terrain Occlusion
    elif (checkbit(packedint, proc_params.QA_CIRRUS1) &
          checkbit(packedint, proc_params.QA_CIRRUS2)):
        return proc_params.QA_CLEAR
    elif checkbit(packedint, proc_params.QA_OCCLUSION):
        return proc_params.QA_CLEAR

    else:
        raise ValueError('Unsupported bitpacked QA value {}'.format(packedint))


def unpackqa(quality, proc_params):
    """
    Transform the bit-packed QA values into their bit offset.
    
    Args:
        quality: 1-d array or list of bit-packed QA values
        proc_params: dictionary of processing parameters

    Returns:
        1-d ndarray
    """

    return np.array([qabitval(q, proc_params) for q in quality])


def count_clear_or_water(quality, clear, water):
    """
    Count clear or water data.

    Arguments:
        quality: quality band values.
        clear: value that represents clear
        water: value that represents water

    Returns:
        int
    """
    return count_value(quality, clear) + count_value(quality, water)


def count_total(quality, fill):
    """
    Count non-fill data.

    Useful for determining ratio of clear:total pixels.

    Arguments:
        quality: quality band values.
        fill: value that represents fill

    Returns:
        int
    """
    return np.sum(~mask_value(quality, fill))


def ratio_clear(quality, clear, water, fill):
    """
    Calculate ratio of clear to non-clear pixels; exclude, fill data.

    Useful for determining ratio of clear:total pixels.

    Arguments:
        quality: quality band values.
        clear: value that represents clear
        water: value that represents water
        fill: value that represents fill

    Returns:
        int
    """
    return (count_clear_or_water(quality, clear, water) /
            count_total(quality, fill))


def ratio_snow(quality, clear, water, snow):
    """Calculate ratio of snow to clear pixels; exclude fill and non-clear data.

    Useful for determining ratio of snow:clear pixels.

    Arguments:
        quality: CFMask quality band values.
        clear: value that represents clear
        water: value that represents water
        snow: value that represents snow

    Returns:
        float: Value between zero and one indicating amount of
            snow-observations.
    """
    snowy_count = count_value(quality, snow)
    clear_count = count_clear_or_water(quality, clear, water)

    return snowy_count / (clear_count + snowy_count + 0.01)


def ratio_cloud(quality, fill, cloud):
    """
    Calculate the ratio of observations that are cloud.

    Args:
        quality: 1-d ndarray of quality information, cannot be bitpacked
        fill: int value representing fill
        cloud: int value representing cloud

    Returns:
        float
    """
    cloud_count = count_value(quality, cloud)
    total = count_total(quality, fill)

    return cloud_count / total


def ratio_water(quality, clear, water):
    """
        Calculate the ratio of observations that are water.

        Args:
            quality: 1-d ndarray of quality information, cannot be bitpacked
            clear: int value representing clear
            water: int value representing water

        Returns:
            float
        """
    clear_count = count_clear_or_water(quality, clear, water)
    water_count = count_value(quality, water)

    return water_count / (clear_count + 0.01)


def enough_clear(quality, clear, water, fill, threshold):
    """
    Determine if clear observations exceed threshold.

    Useful when selecting mathematical model for detection. More clear
    observations allow for models with more coefficients.

    Arguments:
        quality: quality band values.
        clear: value that represents clear
        water: value that represents water
        fill: value that represents fill
        threshold: minimum ratio of clear/water to not-clear/water values.

    Returns:
        boolean: True if >= threshold
    """
    return ratio_clear(quality, clear, water, fill) >= threshold


def enough_snow(quality, clear, water, snow, threshold):
    """
    Determine if snow observations exceed threshold.

    Useful when selecting detection algorithm.

    Arguments:
        quality: quality band values.
        clear: value that represents clear
        water: value that represents water
        snow: value that represents snow
        threshold: minimum ratio of snow to clear/water values.

    Returns:
        boolean: True if >= threshold
    """
    return ratio_snow(quality, clear, water, snow) >= threshold


def filter_median_green(green, filter_range):
    """
    Filter values based on the median value + some range

    Args:
        green: array of green values
        filter_range: value added to the median value, this new result is
                      used as the value for filtering

    Returns:
        1-d boolean ndarray
    """
    median = calc_median(green) + filter_range

    return green < median


def filter_saturated(observations):
    """
    bool index for unsaturated obserervations between 0..10,000

    Useful for efficiently filtering noisy-data from arrays.

    Arguments:
        observations: spectra nd-array, assumed to be shaped as
            (6,n-moments) of unscaled data.
            
    Returns:
        1-d bool ndarray

    """
    unsaturated = ((0 < observations[1, :]) & (observations[1, :] < 10000) &
                   (0 < observations[2, :]) & (observations[2, :] < 10000) &
                   (0 < observations[3, :]) & (observations[3, :] < 10000) &
                   (0 < observations[4, :]) & (observations[4, :] < 10000) &
                   (0 < observations[5, :]) & (observations[5, :] < 10000) &
                   (0 < observations[0, :]) & (observations[0, :] < 10000))
    return unsaturated


def filter_thermal_celsius(thermal, min_celsius=-9320, max_celsius=7070):
    """
    Provide an index of observations within a brightness temperature range.

    Thermal min/max must be provided as a scaled value in degrees celsius.

    The range in unscaled degrees celsius is (-93.2C,70.7C)
    The range in scaled degrees celsius is (-9320, 7070)

    Arguments:
        thermal: 1-d array of thermal values
        min_celsius: minimum temperature in degrees celsius
        max_celsius: maximum temperature in degrees celsius
        
    Returns:
        1-d bool ndarray
    """
    return ((thermal > min_celsius) &
            (thermal < max_celsius))


def standard_procedure_filter(observations, quality, dates, proc_params):
    """
    Filter for the initial stages of the standard procedure.

    Clear or Water
    and Unsaturated

    Temperatures are expected to be in celsius
    Args:
        observations: 2-d ndarray, spectral observations
        quality: 1-d ndarray observation quality information
        dates: 1-d ndarray ordinal observation dates
        proc_params: dictionary of processing parameters

    Returns:
        1-d boolean ndarray
    """
    thermal_idx = proc_params.THERMAL_IDX
    clear = proc_params.QA_CLEAR
    water = proc_params.QA_WATER

    mask = ((mask_value(quality, water) | mask_value(quality, clear)) &
            filter_thermal_celsius(observations[thermal_idx]) &
            filter_saturated(observations))

    date_mask = mask_duplicate_values(dates[mask])

    mask[mask] = date_mask

    return mask


def snow_procedure_filter(observations, quality, dates, proc_params):
    """
    Filter for initial stages of the snow procedure

    Clear or Water
    and Snow

    Args:
        observations: 2-d ndarray, spectral observations
        quality: 1-d ndarray quality information
        dates: 1-d ndarray ordinal observation dates
        thermal_idx: int value identifying the thermal band in the observations
        proc_params: dictionary of processing parameters

    Returns:
        1-d boolean ndarray
    """
    thermal_idx = proc_params.THERMAL_IDX
    clear = proc_params.QA_CLEAR
    water = proc_params.QA_WATER
    snow = proc_params.QA_SNOW

    mask = ((mask_value(quality, water) | mask_value(quality, clear)) &
            filter_thermal_celsius(observations[thermal_idx]) &
            filter_saturated(observations)) | mask_value(quality, snow)

    date_mask = mask_duplicate_values(dates[mask])

    mask[mask] = date_mask

    return mask


def insufficient_clear_filter(observations, quality, dates, proc_params):
    """
    Filter for the initial stages of the insufficient clear procedure.

    The main difference being there is an additional exclusion of observations
    where the green value is > the median green + 400.

    Args:
        observations: 2-d ndarray, spectral observations
        quality: 1-d ndarray quality information
        dates: 1-d ndarray ordinal observation dates
        proc_params: dictionary of processing parameters

    Returns:
        1-d boolean ndarray
    """
    green_idx = proc_params.GREEN_IDX
    filter_range = proc_params.MEDIAN_GREEN_FILTER

    standard_mask = standard_procedure_filter(observations, quality, dates, proc_params)
    green_mask = filter_median_green(observations[:, standard_mask][green_idx], filter_range)

    standard_mask[standard_mask] &= green_mask

    date_mask = mask_duplicate_values(dates[standard_mask])
    standard_mask[standard_mask] = date_mask

    return standard_mask


def quality_probabilities(quality, proc_params):
    """
    Provide probabilities that any given observation falls into one of three
    categories - cloud, snow, or water.

    This is mainly used in further downstream processing, and helps ensure
    consistency.

    Args:
        quality: 1-d ndarray of quality information, cannot be bitpacked
        proc_params: dictionary of global processing parameters

    Returns:
        float probability cloud
        float probability snow
        float probability water
    """
    snow = ratio_snow(quality, proc_params.QA_CLEAR, proc_params.QA_WATER,
                      proc_params.QA_SNOW)

    cloud = ratio_cloud(quality, proc_params.QA_FILL, proc_params.QA_CLOUD)

    water = ratio_water(quality, proc_params.QA_CLEAR, proc_params.QA_WATER)

    return cloud, snow, water
