from sklearn import linear_model
import numpy as np

from ccd.models import FittedModel
from ccd.math_utils import calc_rmse


def __coefficient_cache_key(observation_dates):
    return tuple(observation_dates)


def coefficient_matrix(dates, avg_days_yr, num_coefficients):
    """
    Fourier transform function to be used for the matrix of inputs for
    model fitting

    Args:
        dates: list of ordinal dates
        num_coefficients: how many coefficients to use to build the matrix

    Returns:
        Populated numpy array with coefficient values
    """
    w = 2 * np.pi / avg_days_yr

    matrix = np.zeros(shape=(len(dates), 7), order='F')

    # lookup optimizations
    # Before optimization - 12.53% of total runtime
    # After optimization  - 10.57% of total runtime
    cos = np.cos
    sin = np.sin

    w12 = w * dates
    matrix[:, 0] = dates
    matrix[:, 1] = cos(w12)
    matrix[:, 2] = sin(w12)

    if num_coefficients >= 6:
        w34 = 2 * w12
        matrix[:, 3] = cos(w34)
        matrix[:, 4] = sin(w34)

    if num_coefficients >= 8:
        w56 = 3 * w12
        matrix[:, 5] = cos(w56)
        matrix[:, 6] = sin(w56)

    return matrix


def fitted_model(dates, spectra_obs, max_iter, avg_days_yr, num_coefficients):
    """Create a fully fitted lasso model.

    Args:
        dates: list or ordinal observation dates
        spectra_obs: list of values corresponding to the observation dates for
            a single spectral band
        num_coefficients: how many coefficients to use for the fit
        max_iter: maximum number of iterations that the coefficients
            undergo to find the convergence point.

    Returns:
        sklearn.linear_model.Lasso().fit(observation_dates, observations)

    Example:
        fitted_model(dates, obs).predict(...)
    """
    coef_matrix = coefficient_matrix(dates, avg_days_yr, num_coefficients)

    lasso = linear_model.Lasso(max_iter=max_iter)
    model = lasso.fit(coef_matrix, spectra_obs)

    predictions = model.predict(coef_matrix)
    rmse, residuals = calc_rmse(spectra_obs, predictions, num_pm=num_coefficients)

    return FittedModel(fitted_model=model, rmse=rmse, residual=residuals)


def predict(model, dates, avg_days_yr):
    coef_matrix = coefficient_matrix(dates, avg_days_yr, 8)

    return model.fitted_model.predict(coef_matrix)
