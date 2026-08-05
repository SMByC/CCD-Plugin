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
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Final

import numpy as np

MILLISECONDS_PER_YEAR: Final = 365.25 * 24 * 60 * 60 * 1000
MILLISECONDS_PER_DAY: Final = 24 * 60 * 60 * 1000
CCDC_COEFFICIENT_COUNT: Final = 8

type NumericValue = int | float | None
type ReduceRegionValue = Sequence[Sequence[NumericValue | Sequence[NumericValue]]]


# CCDC reports changeProb per segment: 1 once a break is confirmed, 0 when the segment simply
# ended, and a fraction while a change is still accumulating the consecutive observations
# minObservations demands. Only a fully confirmed break is a real break in the trend.
CONFIRMED_CHANGE_PROBABILITY: Final = 1.0


@dataclass(frozen=True, slots=True)
class ModelSegment:
    number: int
    start_ms: float
    end_ms: float
    break_ms: float | None
    change_probability: float | None
    rmse: float | None
    dates_ms: np.ndarray
    values: np.ndarray

    @property
    def is_confirmed_break(self) -> bool:
        """True when CCDC confirmed the break, not merely started tracking one."""
        if self.break_ms is None or self.change_probability is None:
            return False
        return self.change_probability >= CONFIRMED_CHANGE_PROBABILITY


def evaluate_ccdc_model(timestamps_ms, coefficients: Sequence[float] | np.ndarray):
    """CCDC harmonic model: intercept, linear trend and three annual harmonics.

    Accepts a scalar or an array of timestamps and returns the matching shape, so a whole
    segment is evaluated in one vectorised pass instead of one Python call per sampled date.
    """
    times = np.asarray(timestamps_ms, dtype=float)
    phase = times * (2 * np.pi / MILLISECONDS_PER_YEAR)
    return (
        coefficients[0]
        + coefficients[1] * times
        + coefficients[2] * np.cos(phase)
        + coefficients[3] * np.sin(phase)
        + coefficients[4] * np.cos(2 * phase)
        + coefficients[5] * np.sin(2 * phase)
        + coefficients[6] * np.cos(3 * phase)
        + coefficients[7] * np.sin(3 * phase)
    )


def normalize_observations(
    timeseries: Mapping[str, Sequence[NumericValue]], band: str
) -> tuple[np.ndarray, np.ndarray]:
    pairs = np.asarray(list(zip(timeseries["time"], timeseries[band], strict=True)), dtype=float)
    if pairs.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    finite_pairs = pairs[np.isfinite(pairs).all(axis=1)]
    return finite_pairs[:, 0], finite_pairs[:, 1]


def sample_segment_dates(start_ms: float, end_ms: float, interval_days: int = 5) -> np.ndarray:
    if not np.isfinite(start_ms) or not np.isfinite(end_ms) or end_ms < start_ms:
        return np.array([], dtype=float)
    interval_ms = interval_days * MILLISECONDS_PER_DAY
    sampled_dates = start_ms + np.arange(0.0, end_ms - start_ms, interval_ms, dtype=float)
    return np.append(sampled_dates, end_ms)


def _finite_value(candidate) -> float | None:
    """A real, finite float, or None for the placeholders Earth Engine uses for 'no value'."""
    if not isinstance(candidate, Real):
        return None
    value = float(candidate)
    return value if np.isfinite(value) else None


def build_model_segments(result_info: Mapping[str, ReduceRegionValue], band: str) -> list[ModelSegment]:
    start_layers = result_info.get("tStart", ())
    end_layers = result_info.get("tEnd", ())
    coefficient_layers = result_info.get(f"{band}_coefs", ())
    if not start_layers or not end_layers or not coefficient_layers:
        return []

    start_values, end_values, coefficient_rows = start_layers[0], end_layers[0], coefficient_layers[0]
    break_layers = result_info.get("tBreak", ())
    break_values = break_layers[0] if break_layers else ()
    rmse_layers = result_info.get(f"{band}_rmse", ())
    rmse_values = rmse_layers[0] if rmse_layers else ()
    probability_layers = result_info.get("changeProb", ())
    probability_values = probability_layers[0] if probability_layers else ()
    segment_count = min(len(start_values), len(end_values), len(coefficient_rows))
    segments: list[ModelSegment] = []

    for index in range(segment_count):
        start_ms = _finite_value(start_values[index])
        end_ms = _finite_value(end_values[index])
        coefficient_row = coefficient_rows[index]
        if start_ms is None or end_ms is None or end_ms < start_ms:
            continue
        if not isinstance(coefficient_row, Sequence) or len(coefficient_row) != CCDC_COEFFICIENT_COUNT:
            continue
        if any(_finite_value(coefficient) is None for coefficient in coefficient_row):
            continue

        coefficients = np.asarray(coefficient_row, dtype=float)
        # tBreak is 0 for the last segment, which has no break
        break_ms = _finite_value(break_values[index]) if index < len(break_values) else None
        if break_ms is not None and break_ms <= 0:
            break_ms = None
        rmse = _finite_value(rmse_values[index]) if index < len(rmse_values) else None
        probability = _finite_value(probability_values[index]) if index < len(probability_values) else None

        dates_ms = sample_segment_dates(start_ms, end_ms)
        values = np.asarray(evaluate_ccdc_model(dates_ms, coefficients), dtype=float)
        segments.append(
            ModelSegment(len(segments) + 1, start_ms, end_ms, break_ms, probability, rmse, dates_ms, values)
        )

    return segments
