import logging
import numpy as np

from ccd.models import robust_fit


log = logging.getLogger(__name__)


def tmask_coefficient_matrix(dates, avg_days_yr):
    """Coefficient matrix that is used for Tmask modeling

    Args:
        dates: list of ordinal julian dates

    Returns:
        Populated numpy array with coefficient values
    """
    annual_cycle = 2*np.pi/avg_days_yr
    observation_cycle = annual_cycle / np.ceil((dates[-1] - dates[0]) / avg_days_yr)

    matrix = np.ones(shape=(dates.shape[0], 5), order='F')
    matrix[:, 0] = np.cos(annual_cycle * dates)
    matrix[:, 1] = np.sin(annual_cycle * dates)
    matrix[:, 2] = np.cos(observation_cycle * dates)
    matrix[:, 3] = np.sin(observation_cycle * dates)

    return matrix


def tmask(dates, observations, variogram, bands, t_const, avg_days_yr):
    """Produce an index for filtering outliers.

    Arguments:
        dates: ordinal date values associated to each n-moment in the
            observations
        observations: spectral values, assumed to be shaped as
            (n-bands, n-moments)
        bands: list of band indices used for outlier detection, by default
            bands 2 and 5.
        t_const: constant used to scale a variogram value for thresholding on
            whether a value is an outlier or not

    Return: indexed array, excluding outlier observations.
    """
    # variogram = calculate_variogram(observations)
    # Time and expected values using a four-part matrix of coefficients.
    # regression = lm.LinearRegression()
    regression = robust_fit.RLM(maxiter=5)

    tmask_matrix = tmask_coefficient_matrix(dates, avg_days_yr)

    # Accumulator for outliers. This starts off as a list of False values
    # because we don't assume anything is an outlier.
    _, sample_count = observations.shape
    outliers = np.zeros(sample_count, dtype=bool)

    # For each band, determine if the delta between predeicted and actual
    # values exceeds the threshold. If it does, then it is an outlier.
    for band_ix in bands:
        fit = regression.fit(tmask_matrix, observations[band_ix])
        predicted = fit.predict(tmask_matrix)
        outliers += np.abs(predicted - observations[band_ix]) > variogram[band_ix] * t_const

    # Keep all observations that aren't outliers.
    return outliers
    # return dates[~outliers], observations[:, ~outliers]
