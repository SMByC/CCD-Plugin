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
from dataclasses import dataclass, field
from numbers import Real
from typing import Final, TypeAlias

import numpy as np

from .gee_common import FULL_YEAR

MILLISECONDS_PER_YEAR: Final = 365.25 * 24 * 60 * 60 * 1000
MILLISECONDS_PER_DAY: Final = 24 * 60 * 60 * 1000
CCDC_COEFFICIENT_COUNT: Final = 8
MODEL_SAMPLE_DAYS: Final = 5
# A season of the model shorter than this is drawn as a point at its middle: as a line it is too
# short to see, a few days on an axis of decades, and a one-day season is a single instant. It is
# also less than one 16-day Landsat revisit, so it holds one observation per pass at most.
SHORT_SEASON_DAYS: Final = 16

# Spelled with TypeAlias rather than the PEP 695 `type` statement, which is a hard SyntaxError
# before 3.12. The plugin's floor is 3.11, set by datetime.UTC and typing.assert_never in
# plot.py; ruff's target-version is pinned there too, so UP040 does not push this back.
NumericValue: TypeAlias = int | float | None
ReduceRegionValue: TypeAlias = Sequence[Sequence[NumericValue | Sequence[NumericValue]]]


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
    # samples to draw as a point, one in the middle of each season too short to draw as a line
    points: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))

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


def utc_dates(timestamps_ms) -> np.ndarray:
    """Unix-millisecond timestamps as naive UTC datetime64 values, the form plotly takes in bulk."""
    return np.floor(np.asarray(timestamps_ms, dtype=float)).astype("int64").astype("datetime64[ms]")


def count_observation_dates(timestamps_ms) -> int:
    """How many distinct UTC days the observations fall on.

    A point in the overlap of two Landsat rows or two Sentinel-2 tiles is imaged twice on the same
    day, seconds apart. Earth Engine's CCDC keeps one observation per day (measured: the same fit
    with and without the duplicates), so the day count is what the fit actually used.
    """
    return int(np.unique(utc_dates(timestamps_ms).astype("datetime64[D]")).size)


def within_doy_window(timestamps_ms, doy_range) -> np.ndarray:
    """Which timestamps fall inside a day-of-year window, as gee_common.date_and_doy_filter selects.

    Both ends are inclusive, days are UTC, and a window whose start is after its end wraps the new
    year. No window, or the full year, keeps everything.
    """
    dates = utc_dates(timestamps_ms)
    if doy_range is None or tuple(doy_range) == FULL_YEAR:
        return np.ones(dates.shape, dtype=bool)
    start, end = doy_range
    day_of_year = (dates.astype("datetime64[D]") - dates.astype("datetime64[Y]")).astype("int64") + 1
    if start <= end:
        return (day_of_year >= start) & (day_of_year <= end)
    return (day_of_year >= start) | (day_of_year <= end)


def sample_segment_dates(start_ms: float, end_ms: float, interval_days: int = MODEL_SAMPLE_DAYS) -> np.ndarray:
    if not np.isfinite(start_ms) or not np.isfinite(end_ms) or end_ms < start_ms:
        return np.array([], dtype=float)
    interval_ms = interval_days * MILLISECONDS_PER_DAY
    sampled_dates = start_ms + np.arange(0.0, end_ms - start_ms, interval_ms, dtype=float)
    return np.append(sampled_dates, end_ms)


def _year_start_ms(year: int) -> float:
    return float(np.datetime64(year - 1970, "Y").astype("datetime64[ms]").astype("int64"))


def doy_window_spans(start_ms: float, end_ms: float, doy_range) -> list[tuple[float, float]]:
    """The parts of [start_ms, end_ms] inside a day-of-year window, one per season, in order.

    Each is a (first, last) millisecond pair, both inside the window: the days are those
    within_doy_window keeps, so a window whose start is after its end runs across the new year.
    """
    start_doy, end_doy = doy_range
    first_year, last_year = (
        int(year) + 1970 for year in utc_dates([start_ms, end_ms]).astype("datetime64[Y]").astype("int64")
    )
    spans = []
    # from the year before: a season across the new year can open then and close inside the range
    for year in range(first_year - 1, last_year + 1):
        opens = _year_start_ms(year) + (start_doy - 1) * MILLISECONDS_PER_DAY
        closing_year = year if start_doy <= end_doy else year + 1
        closes = _year_start_ms(closing_year) + end_doy * MILLISECONDS_PER_DAY - 1
        first, last = max(opens, start_ms), min(closes, end_ms)
        if first <= last:
            spans.append((first, last))
    return spans


def sample_season_dates(start_ms: float, end_ms: float, doy_range) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Dates to evaluate the model at inside a day-of-year window, as (dates, gaps, points).

    Each season is sampled from its first to its last day, so even a one-day season is two samples
    rather than none. A date marked in `gaps` separates two seasons and gets no value, which breaks
    the line there; `points` marks the middle of each season too short to draw as a line.
    """
    dates, gaps, points = [], [], []
    previous_last = None
    for first, last in doy_window_spans(start_ms, end_ms, doy_range):
        if previous_last is not None:
            dates.append([(previous_last + first) / 2])
            gaps.append([True])
            points.append([False])
        season = sample_segment_dates(first, last)
        middle = np.zeros(season.size, dtype=bool)
        if last - first < SHORT_SEASON_DAYS * MILLISECONDS_PER_DAY:
            middle[np.argmin(np.abs(season - (first + last) / 2))] = True
        dates.append(season)
        gaps.append(np.zeros(season.size, dtype=bool))
        points.append(middle)
        previous_last = last
    if not dates:
        return np.array([], dtype=float), np.array([], dtype=bool), np.array([], dtype=bool)
    return np.concatenate(dates).astype(float), np.concatenate(gaps), np.concatenate(points)


def _finite_value(candidate) -> float | None:
    """A real, finite float, or None for the placeholders Earth Engine uses for 'no value'."""
    if not isinstance(candidate, Real):
        return None
    value = float(candidate)
    return value if np.isfinite(value) else None


def build_model_segments(
    result_info: Mapping[str, ReduceRegionValue], band: str, doy_range: tuple[int, int] | None = None
) -> list[ModelSegment]:
    """The fitted segments of `band`, each sampled every few days across its span.

    With a day-of-year window the model is drawn inside it only, one piece per season with a gap
    (NaN) between them: the harmonics were fitted to observations inside the window only, so the
    curve between two seasons is unconstrained extrapolation, not something the data says.
    """
    seasonal = doy_range is not None and tuple(doy_range) != FULL_YEAR
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

        if seasonal:
            dates_ms, gaps, points = sample_season_dates(start_ms, end_ms, doy_range)
        else:
            dates_ms = sample_segment_dates(start_ms, end_ms)
            gaps = points = np.zeros(dates_ms.size, dtype=bool)
        values = np.asarray(evaluate_ccdc_model(dates_ms, coefficients), dtype=float)
        values[gaps] = np.nan
        segments.append(
            ModelSegment(len(segments) + 1, start_ms, end_ms, break_ms, probability, rmse, dates_ms, values, points)
        )

    return segments
