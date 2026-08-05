import math
import unittest
from itertools import pairwise

import numpy as np

from core.plot import (
    MILLISECONDS_PER_YEAR,
    MODEL_COLORS,
    MODEL_LEGEND_COLOR,
    ModelSegment,
    PlotSpec,
    build_figure,
    build_model_segments,
    evaluate_ccdc_model,
    normalize_observations,
    sample_segment_dates,
)


class PlotHelpersTest(unittest.TestCase):
    def test_evaluate_uses_intercept_slope_and_ordered_harmonics(self):
        # Given: a quarter-year timestamp and a coefficient only for sin1.
        coefficients = np.array([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
        timestamp_ms = MILLISECONDS_PER_YEAR / 4

        # When: the CCDC model is evaluated at that timestamp.
        value = evaluate_ccdc_model(timestamp_ms, coefficients)

        # Then: sin1 contributes +1 at phase pi/2.
        self.assertAlmostEqual(value, 1.0, places=12)

    def test_evaluate_all_coefficients_matches_independent_formula(self):
        # Given: all eight coefficients and a timestamp with nontrivial phases.
        coefficients = np.array([1.5, 0.0002, 2.0, -3.0, 4.0, -5.0, 6.0, -7.0])
        timestamp_ms = 123456789.0
        omega = 2 * np.pi / MILLISECONDS_PER_YEAR
        expected = coefficients[0] + coefficients[1] * timestamp_ms
        for harmonic in (1, 2, 3):
            phase = timestamp_ms * harmonic * omega
            expected += coefficients[2 * harmonic] * np.cos(phase)
            expected += coefficients[2 * harmonic + 1] * np.sin(phase)

        # When: the CCDC model is evaluated.
        value = evaluate_ccdc_model(timestamp_ms, coefficients)

        # Then: the result uses the independent cosine/sine harmonic formula.
        self.assertAlmostEqual(value, expected, places=12)

    def test_normalize_observations_filters_nonfinite_pairs_in_order(self):
        # Given: observations containing None, NaN, infinity, and valid pairs.
        timeseries = {
            "time": [1000, None, 3000, 4000, 5000, 6000, 7000],
            "B4": [1.0, 2.0, np.nan, 4.0, np.inf, -np.inf, 7.0],
        }

        # When: observations are normalized for the selected band.
        times_ms, values = normalize_observations(timeseries, "B4")

        # Then: invalid pairs are removed without changing valid order or pairing.
        np.testing.assert_array_equal(times_ms, np.array([1000.0, 4000.0, 7000.0]))
        np.testing.assert_array_equal(values, np.array([1.0, 4.0, 7.0]))

    def test_sample_segment_dates_includes_endpoints_and_short_segments(self):
        # Given: a five-day interval and several segment boundary shapes.
        day_ms = 24 * 60 * 60 * 1000
        start_ms = 10 * day_ms
        end_ms = 23 * day_ms

        # When: dates are sampled for a normal, short, and identical segment.
        normal = sample_segment_dates(start_ms, end_ms, interval_days=5)
        short = sample_segment_dates(start_ms, start_ms + day_ms, interval_days=5)
        identical = sample_segment_dates(start_ms, start_ms, interval_days=5)

        # Then: every valid segment includes both boundaries with no duplicates.
        np.testing.assert_array_equal(normal, np.array([start_ms, 15 * day_ms, 20 * day_ms, end_ms]))
        np.testing.assert_array_equal(short, np.array([start_ms, start_ms + day_ms]))
        np.testing.assert_array_equal(identical, np.array([start_ms]))

    def test_sample_segment_dates_rejects_reversed_and_nonfinite_ranges(self):
        # Given: invalid segment bounds.
        # When: dates are sampled for reversed and non-finite ranges.
        reversed_dates = sample_segment_dates(20.0, 10.0, interval_days=5)
        nonfinite_dates = sample_segment_dates(math.nan, 10.0, interval_days=5)

        # Then: invalid ranges produce empty arrays rather than exceptions.
        self.assertEqual(reversed_dates.size, 0)
        self.assertEqual(nonfinite_dates.size, 0)

    def test_build_model_segments_uses_bounds_and_zero_second_break(self):
        # Given: two valid rows, where the second segment has tBreak=0.
        day_ms = 24 * 60 * 60 * 1000
        result_info = {
            "tStart": [[0.0, 10.0 * day_ms]],
            "tEnd": [[10.0 * day_ms, 20.0 * day_ms]],
            "tBreak": [[7.0 * day_ms, 0.0]],
            "changeProb": [[1.0, 0.0]],
            "B4_coefs": [[[1.0] * 8, [2.0] * 8]],
        }

        # When: model segments are built for B4.
        segments = build_model_segments(result_info, "B4")

        # Then: both tStart/tEnd rows are used, and zero is not a break.
        self.assertEqual(len(segments), 2)
        self.assertIsInstance(segments[0], ModelSegment)
        self.assertEqual(segments[0].number, 1)
        self.assertEqual(segments[0].break_ms, 7.0 * day_ms)
        self.assertIsNone(segments[1].break_ms)
        self.assertEqual(segments[1].start_ms, 10.0 * day_ms)
        self.assertEqual(segments[1].end_ms, 20.0 * day_ms)
        self.assertIn(0.0, segments[0].dates_ms)
        self.assertIn(10.0 * day_ms, segments[0].dates_ms)
        self.assertIn(10.0 * day_ms, segments[1].dates_ms)
        self.assertIn(20.0 * day_ms, segments[1].dates_ms)

    def test_build_model_segments_skips_malformed_rows_and_reversed_bounds(self):
        # Given: rows with bad coefficient lengths, non-finite coefficients, and reversed bounds.
        result_info = {
            "tStart": [[0.0, 10.0, 30.0, 40.0]],
            "tEnd": [[10.0, 20.0, 25.0, 50.0]],
            "tBreak": [[0.0, 0.0, 0.0, 0.0]],
            "B4_coefs": [[[1.0] * 7, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, np.inf], [1.0] * 8, [2.0] * 8]],
        }

        # When: malformed model rows are converted into segments.
        segments = build_model_segments(result_info, "B4")

        # Then: only the valid, forward-bounded row is retained.
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].start_ms, 40.0)
        self.assertEqual(segments[0].end_ms, 50.0)


class PlotFigureTest(unittest.TestCase):
    def test_build_figure_has_observation_segments_and_confirmed_break_shape(self):
        # Given: finite observations and two model segments with one confirmed break.
        day_ms = 24 * 60 * 60 * 1000
        timeseries = {"time": [0.0, day_ms, 2.0 * day_ms], "B4": [1.0, 2.0, 3.0]}
        ccdc_result_info = {
            "tStart": [[0.0, 10.0 * day_ms]],
            "tEnd": [[10.0 * day_ms, 20.0 * day_ms]],
            "tBreak": [[7.0 * day_ms, 0.0]],
            "changeProb": [[1.0, 0.0]],
            "B4_coefs": [[[1.0] * 8, [2.0] * 8]],
        }
        spec = PlotSpec(dataset="Landsat", band="B4", longitude=-75.0, latitude=5.0)

        # When: the pure Plotly figure is built.
        figure = build_figure(ccdc_result_info, timeseries, spec)

        # Then: observations, one legend proxy and the two model segments are the data traces.
        self.assertEqual(len(figure.data), 4)
        self.assertEqual(sum(trace.mode == "markers" for trace in figure.data), 1)
        self.assertEqual(sum(trace.mode == "lines" for trace in figure.data), 3)
        model_traces = [trace for trace in figure.data if trace.mode == "lines"]
        self.assertEqual(len({trace.legendgroup for trace in model_traces}), 1)
        # exactly one legend entry for the whole group, and it is the neutral proxy
        self.assertEqual(sum(trace.showlegend is True for trace in model_traces), 1)
        legend_entry = next(trace for trace in model_traces if trace.showlegend)
        self.assertEqual(legend_entry.line.color, MODEL_LEGEND_COLOR)
        self.assertEqual(list(legend_entry.x), [None])
        self.assertEqual(len(figure.layout.shapes), 1)
        self.assertTrue(figure.layout.shapes[0].showlegend)
        self.assertEqual(figure.layout.shapes[0].name, "Change")

    def test_unconfirmed_break_is_not_drawn_as_a_confirmed_change(self):
        # Given: a single segment whose tBreak is set but whose change is still accumulating,
        # which is what CCDC reports when a series ends mid-change.
        day_ms = 24 * 60 * 60 * 1000
        timeseries = {"time": [0.0, day_ms], "B4": [1.0, 2.0]}
        ccdc_result_info = {
            "tStart": [[0.0]],
            "tEnd": [[10.0 * day_ms]],
            "tBreak": [[9.0 * day_ms]],
            "changeProb": [[0.17]],
            "B4_coefs": [[[1.0] * 8]],
        }
        spec = PlotSpec(dataset="Landsat", band="B4", longitude=0.0, latitude=0.0)

        # When: the pure Plotly figure is built.
        figure = build_figure(ccdc_result_info, timeseries, spec)

        # Then: it is drawn, but as "Change in progress" rather than a confirmed change.
        self.assertEqual(len(figure.layout.shapes), 1)
        self.assertEqual(figure.layout.shapes[0].name, "In progress")
        self.assertEqual(figure.layout.shapes[0].line.dash, "dot")
        self.assertIn("0 breaks", figure.layout.title.text)
        self.assertIn("1 in progress", figure.layout.title.text)

    def test_break_without_change_probability_is_not_reported_as_confirmed(self):
        # Given: a result with no changeProb layer at all (older cached results).
        day_ms = 24 * 60 * 60 * 1000
        ccdc_result_info = {
            "tStart": [[0.0]],
            "tEnd": [[10.0 * day_ms]],
            "tBreak": [[9.0 * day_ms]],
            "B4_coefs": [[[1.0] * 8]],
        }

        # When: segments are built.
        segments = build_model_segments(ccdc_result_info, "B4")

        # Then: the break is kept but cannot claim to be confirmed.
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].break_ms, 9.0 * day_ms)
        self.assertIsNone(segments[0].change_probability)
        self.assertFalse(segments[0].is_confirmed_break)

    def test_build_figure_has_model_segments_without_break_shapes(self):
        # Given: finite observations and model segments with no confirmed breaks.
        day_ms = 24 * 60 * 60 * 1000
        timeseries = {"time": [0.0, day_ms], "B4": [1.0, 2.0]}
        ccdc_result_info = {
            "tStart": [[0.0, 10.0 * day_ms]],
            "tEnd": [[10.0 * day_ms, 20.0 * day_ms]],
            "tBreak": [[0.0, 0.0]],
            "B4_coefs": [[[1.0] * 8, [2.0] * 8]],
        }
        spec = PlotSpec(dataset="Landsat", band="B4", longitude=0.0, latitude=0.0)

        # When: the pure Plotly figure is built.
        figure = build_figure(ccdc_result_info, timeseries, spec)

        # Then: model traces remain, but no break shapes are emitted.
        self.assertEqual(sum(trace.mode == "lines" for trace in figure.data), 3)
        self.assertEqual(len(figure.layout.shapes), 0)

    def test_consecutive_segments_are_drawn_in_different_colours(self):
        # Given: more model segments than there are palette slots.
        day_ms = 24 * 60 * 60 * 1000
        count = len(MODEL_COLORS) + 1
        result_info = {
            "tStart": [[index * 10.0 * day_ms for index in range(count)]],
            "tEnd": [[(index + 1) * 10.0 * day_ms for index in range(count)]],
            "tBreak": [[0.0] * count],
            "changeProb": [[0.0] * count],
            "B4_coefs": [[[1.0] * 8 for _ in range(count)]],
        }
        spec = PlotSpec(dataset="Landsat", band="B4", longitude=0.0, latitude=0.0)

        # When: the figure is built.
        figure = build_figure(result_info, {"time": [], "B4": []}, spec)

        # Then: no two neighbouring segments share a colour, and the palette cycles past its end.
        segment_colors = [
            trace.line.color for trace in figure.data if trace.mode == "lines" and trace.showlegend is False
        ]
        self.assertEqual(len(segment_colors), count)
        for previous, current in pairwise(segment_colors):
            self.assertNotEqual(previous, current)
        self.assertEqual(segment_colors[0], segment_colors[len(MODEL_COLORS)])

    def test_build_figure_empty_input_has_annotation_and_no_data_traces(self):
        # Given: empty observations, an empty selected band, and no model result.
        timeseries = {"time": [], "B4": []}
        spec = PlotSpec(dataset="Landsat", band="B4", longitude=0.0, latitude=0.0)

        # When: the pure Plotly figure is built.
        figure = build_figure({}, timeseries, spec)

        # Then: the empty figure has no data traces and retains an annotation.
        self.assertEqual(len(figure.data), 0)
        self.assertGreaterEqual(len(figure.layout.annotations), 1)


if __name__ == "__main__":
    unittest.main()
