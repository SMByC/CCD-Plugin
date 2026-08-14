import ast
import math
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path

import numpy as np

import core.plot as plot_module
from core.plot import (
    BACKGROUND_COLOR,
    CHANGE_COLOR,
    DARK_THEME,
    GRID_COLOR,
    LIGHT_THEME,
    MILLISECONDS_PER_YEAR,
    MODEL_COLORS,
    MODEL_LEGEND_COLOR,
    MUTED_TEXT_COLOR,
    OBSERVATION_COLOR,
    OVERLAY_BACKGROUND,
    PENDING_CHANGE_COLOR,
    TEXT_COLOR,
    ModelSegment,
    PlotSpec,
    PlotStyle,
    _page_theme_script,
    build_figure,
    build_model_segments,
    evaluate_ccdc_model,
    normalize_observations,
    sample_segment_dates,
    write_plot_html,
)


def _representative_figure(style=PlotStyle.LIGHT):
    day_ms = 24 * 60 * 60 * 1000
    result_info = {
        "tStart": [[0.0, 10.0 * day_ms]],
        "tEnd": [[10.0 * day_ms, 20.0 * day_ms]],
        "tBreak": [[7.0 * day_ms, 17.0 * day_ms]],
        "changeProb": [[1.0, 0.4]],
        "B4_coefs": [[[1.0] * 8, [2.0] * 8]],
    }
    timeseries = {"time": [0.0, day_ms], "B4": [1.0, 2.0]}
    spec = PlotSpec(dataset="Landsat", band="B4", longitude=-75.0, latitude=5.0)
    return build_figure(result_info, timeseries, spec, style=style)


def _relative_luminance(hex_color: str) -> float:
    channels = tuple(int(hex_color[offset : offset + 2], 16) / 255 for offset in (1, 3, 5))
    linear = tuple(
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels
    )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted((_relative_luminance(foreground), _relative_luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class PlotHelpersTest(unittest.TestCase):
    def test_plot_module_parses_with_python_3_11_grammar(self):
        source_path = Path(plot_module.__file__)

        ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path), feature_version=(3, 11))

    def test_resolve_plot_style_explicit_value_overrides_opposite_fallback(self):
        # Given: each explicit canonical style and its opposite fallback.
        for value, fallback, expected in (
            ("light", PlotStyle.DARK, PlotStyle.LIGHT),
            ("dark", PlotStyle.LIGHT, PlotStyle.DARK),
        ):
            with self.subTest(value=value):
                # When: the retained plot style is resolved.
                resolved = plot_module.resolve_plot_style(value, fallback)

                # Then: the explicit style takes precedence.
                self.assertEqual(resolved, expected)

    def test_resolve_plot_style_uses_fallback_when_value_is_none(self):
        # Given: no retained style and a canonical fallback.
        # When: the retained plot style is resolved.
        resolved = plot_module.resolve_plot_style(None, PlotStyle.DARK)

        # Then: the fallback is returned unchanged.
        self.assertIs(resolved, PlotStyle.DARK)

    def test_resolve_plot_style_rejects_invalid_value(self):
        # Given: a retained style outside the canonical values.
        # When: the retained plot style is resolved.
        # Then: invalid input raises ValueError.
        with self.assertRaises(ValueError):
            plot_module.resolve_plot_style("sepia", PlotStyle.LIGHT)

    def test_evaluate_uses_intercept_slope_and_ordered_harmonics(self):
        # Given: a quarter-year timestamp and a coefficient only for sin1.
        coefficients = np.array([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
        timestamp_ms = MILLISECONDS_PER_YEAR / 4

        # When: the CCDC model is evaluated at that timestamp.
        value = evaluate_ccdc_model(timestamp_ms, coefficients)

        # Then: sin1 contributes +1 at phase pi/2.
        self.assertAlmostEqual(float(value), 1.0, places=12)

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
    def test_light_theme_preserves_existing_exact_color_values(self):
        # Given: the established Light chart palette.
        expected = (
            "#3F83B5",
            ("#50a064", "#8b66b3", "#979836", "#1b8abd", "#bb6690"),
            "#8A8F98",
            "#C45C5C",
            "#D9A566",
            "#E4EAF0",
            "#334155",
            "#64748B",
            "#FFFFFF",
            "rgba(255, 255, 255, 0.72)",
        )

        # When: the default figure is built.
        figure = _representative_figure()

        # Then: every pre-existing Light color remains exact.
        self.assertEqual(
            (
                OBSERVATION_COLOR,
                MODEL_COLORS,
                MODEL_LEGEND_COLOR,
                CHANGE_COLOR,
                PENDING_CHANGE_COLOR,
                GRID_COLOR,
                TEXT_COLOR,
                MUTED_TEXT_COLOR,
                BACKGROUND_COLOR,
                OVERLAY_BACKGROUND,
            ),
            expected,
        )
        self.assertEqual(figure.layout.paper_bgcolor, BACKGROUND_COLOR)
        self.assertEqual(figure.data[0].marker.color, OBSERVATION_COLOR)
        self.assertEqual([shape.line.color for shape in figure.layout.shapes], [CHANGE_COLOR, PENDING_CHANGE_COLOR])
        self.assertEqual(LIGHT_THEME.control_background, OVERLAY_BACKGROUND)
        self.assertEqual(LIGHT_THEME.control_text_color, TEXT_COLOR)
        self.assertEqual(LIGHT_THEME.control_border_color, GRID_COLOR)
        self.assertEqual(figure.layout.updatemenus[0].bgcolor, LIGHT_THEME.control_background)
        self.assertEqual(figure.layout.updatemenus[0].font.color, LIGHT_THEME.control_text_color)
        self.assertEqual(figure.layout.updatemenus[0].bordercolor, LIGHT_THEME.control_border_color)

    def test_theme_controls_restore_historical_state_colors(self):
        # Given: the historical Plotly control palette for both chart styles.
        expected_light_states = ("#F4FAFF", "#F4FAFF")
        expected_dark_states = ("#2C333A", "#E6EDF3", "#4C5864", "#3B4652", "#46535E")
        actual = (
            DARK_THEME.control_background,
            DARK_THEME.control_text_color,
            DARK_THEME.control_border_color,
            DARK_THEME.control_active_background,
            DARK_THEME.control_hover_background,
        )

        # When/Then: every restored state is exact and Dark remains WCAG AA.
        self.assertEqual(
            (LIGHT_THEME.control_active_background, LIGHT_THEME.control_hover_background),
            expected_light_states,
        )
        self.assertEqual(actual, expected_dark_states)
        for background in (
            DARK_THEME.control_background,
            DARK_THEME.control_active_background,
            DARK_THEME.control_hover_background,
        ):
            with self.subTest(background=background):
                self.assertGreaterEqual(_contrast_ratio(DARK_THEME.control_text_color, background), 4.5)

    def test_dark_style_is_applied_to_complete_representative_figure(self):
        # Given: a figure containing observations, models, and both break states.
        # When: it is built with Dark as its initial style.
        figure = _representative_figure(PlotStyle.DARK)

        # Then: all visible color-bearing elements use the Dark theme.
        self.assertEqual(figure.layout.paper_bgcolor, DARK_THEME.background_color)
        self.assertEqual(figure.layout.plot_bgcolor, DARK_THEME.background_color)
        self.assertEqual(figure.layout.font.color, DARK_THEME.text_color)
        self.assertIn(DARK_THEME.muted_text_color, figure.layout.title.text)
        self.assertEqual(figure.data[0].marker.color, DARK_THEME.observation_color)
        self.assertEqual(figure.data[1].line.color, DARK_THEME.model_legend_color)
        self.assertEqual(
            [trace.line.color for trace in figure.data[2:]],
            list(DARK_THEME.model_colors[:2]),
        )
        self.assertEqual(
            [shape.line.color for shape in figure.layout.shapes],
            [DARK_THEME.change_color, DARK_THEME.pending_change_color],
        )
        self.assertEqual(
            [annotation.bgcolor for annotation in figure.layout.annotations],
            [DARK_THEME.overlay_background, DARK_THEME.overlay_background],
        )
        self.assertEqual(figure.layout.updatemenus[0].bgcolor, DARK_THEME.control_background)
        self.assertEqual(figure.layout.updatemenus[0].font.color, DARK_THEME.control_text_color)
        self.assertEqual(figure.layout.updatemenus[0].bordercolor, DARK_THEME.control_border_color)

    def test_page_shell_script_initializes_and_tracks_theme_buttons(self):
        # Given: HTML documents serialized from each initial chart style.
        for style, initial_background in (
            (PlotStyle.LIGHT, BACKGROUND_COLOR),
            (PlotStyle.DARK, DARK_THEME.background_color),
        ):
            with self.subTest(style=style):
                plot_id = f"ccd-theme-{style.value}"

                # When: Plotly scopes the post script to the generated graph div.
                html = _representative_figure(style).to_html(
                    full_html=True,
                    include_plotlyjs=False,
                    post_script=_page_theme_script(style),
                    div_id=plot_id,
                )

                # Then: root and body start in sync and public Plotly button events remain wired.
                self.assertIn(f'document.getElementById("{plot_id}")', html)
                self.assertNotIn("{plot_id}", html)
                self.assertIn("document.documentElement.style.backgroundColor", html)
                self.assertIn("document.body.style.backgroundColor", html)
                self.assertIn(f'setPageBackground("{initial_background}")', html)
                self.assertIn(f'"Light": "{BACKGROUND_COLOR}"', html)
                self.assertIn(f'"Dark": "{DARK_THEME.background_color}"', html)
                self.assertIn('graphDiv.on("plotly_buttonclicked"', html)
                self.assertIn('graphDiv.on("plotly_afterplot"', html)
                self.assertIn("backgrounds[event.button.label]", html)
                self.assertIn("window.location.hash = event.button.label.toLowerCase()", html)

    def test_page_shell_script_restores_scoped_compact_controls_with_public_state(self):
        # Given: the post-render script for either initial theme.
        for style, active_index in ((PlotStyle.LIGHT, 0), (PlotStyle.DARK, 1)):
            with self.subTest(style=style):
                script = _page_theme_script(style)

                # When/Then: local state drives guarded, graph-scoped generated-node adjustments.
                self.assertIn(f"let activeStyleIndex = {active_index}", script)
                self.assertIn("activeStyleIndex = event.active", script)
                self.assertNotIn("_fullLayout", script)
                self.assertNotIn("document.querySelector", script)
                for selector in (".updatemenu-button", ".updatemenu-item-rect", ".updatemenu-item-text"):
                    self.assertIn(f'graphDiv.querySelectorAll("{selector}")', script)
                self.assertIn("!buttons.length", script)
                self.assertIn("buttons.length !== rects.length", script)
                self.assertIn("buttons.length !== texts.length", script)
                self.assertIn("horizontalPadding = 7", script)
                self.assertIn("minimumWidth = 30", script)
                self.assertIn("buttonGap = 2", script)
                self.assertIn("visibleHeight = 20", script)
                self.assertIn("textBaselineY = 13", script)
                self.assertIn("text.getBBox().width + 2 * horizontalPadding", script)
                self.assertIn('graphDiv.on("plotly_buttonclicked"', script)
                self.assertIn('graphDiv.on("plotly_afterplot"', script)
                self.assertIn("requestAnimationFrame", script)
                self.assertIn("ccd-theme-active", script)
                self.assertIn(f"fill: {DARK_THEME.control_active_background} !important", script)
                self.assertIn(f"fill: {DARK_THEME.control_hover_background} !important", script)

    def test_self_contained_writer_embeds_plotly_without_remote_or_sibling_assets(self):
        # Given: a destination for a representative interactive figure.
        with tempfile.TemporaryDirectory() as temporary_directory:
            html_path = Path(temporary_directory) / "plot.html"

            # When: the QWebEngine page is serialized for offline use.
            write_plot_html(
                _representative_figure(),
                html_path,
                image_filename="ccd_b4",
                style=PlotStyle.LIGHT,
            )
            document = html_path.read_text(encoding="utf-8")

            # Then: Plotly and plugin behavior are inline and no external script is required.
            self.assertGreater(len(document), 1_000_000)
            self.assertIn("plotly.js", document.lower())
            self.assertNotRegex(document, r"<script[^>]+src=")
            self.assertNotRegex(document, r"<script[^>]+mathjax")
            self.assertIn('graphDiv.on("plotly_buttonclicked"', document)

    def test_theme_toggle_has_exactly_two_ordered_update_buttons(self):
        # Given: figures initialized in each supported style.
        self.assertEqual(list(PlotStyle), [PlotStyle.LIGHT, PlotStyle.DARK])
        for style, active in ((PlotStyle.LIGHT, 0), (PlotStyle.DARK, 1)):
            with self.subTest(style=style):
                # When: the figure's theme control is inspected.
                menu = _representative_figure(style).layout.updatemenus[0]

                # Then: it is a horizontal two-style control with the matching active button.
                self.assertEqual([button.label for button in menu.buttons], ["Light", "Dark"])
                self.assertEqual([button.method for button in menu.buttons], ["update", "update"])
                self.assertEqual(menu.active, active)
                self.assertTrue(menu.showactive)
                self.assertEqual(menu.direction, "right")
                self.assertEqual(menu.x, 0)
                self.assertEqual(menu.y, 1)
                self.assertEqual(menu.xanchor, "left")
                self.assertEqual(menu.yanchor, "bottom")
                self.assertEqual(menu.font.size, 10)

    def test_legend_is_anchored_to_the_plot_area_top_right(self):
        # Given: the representative plot's responsive in-plot legend.
        legend = _representative_figure().layout.legend

        # When/Then: paper coordinates keep it at the plot area's top-right corner.
        self.assertEqual(legend.orientation, "h")
        self.assertEqual(legend.x, 1)
        self.assertEqual(legend.xref, "paper")
        self.assertEqual(legend.xanchor, "right")
        self.assertEqual(legend.y, 1)
        self.assertEqual(legend.yref, "paper")
        self.assertEqual(legend.yanchor, "top")

    def test_theme_buttons_carry_complete_reversible_payloads(self):
        # Given: the complete expected targets for both styles.
        expected_light_traces = [OBSERVATION_COLOR, MODEL_LEGEND_COLOR, *MODEL_COLORS[:2]]
        expected_dark_traces = [
            DARK_THEME.observation_color,
            DARK_THEME.model_legend_color,
            *DARK_THEME.model_colors[:2],
        ]

        # When/Then: either initial style can make a complete round trip in both directions.
        for initial_style in PlotStyle:
            with self.subTest(initial_style=initial_style):
                figure = _representative_figure(initial_style)
                light_data, light_layout = figure.layout.updatemenus[0].buttons[0].args
                dark_data, dark_layout = figure.layout.updatemenus[0].buttons[1].args
                self.assertEqual(list(light_data["marker.color"]), expected_light_traces)
                self.assertEqual(list(light_data["line.color"]), expected_light_traces)
                self.assertEqual(list(dark_data["marker.color"]), expected_dark_traces)
                self.assertEqual(list(dark_data["line.color"]), expected_dark_traces)
                for payload, target in ((light_layout, LIGHT_THEME), (dark_layout, DARK_THEME)):
                    self.assertEqual(payload["paper_bgcolor"], target.background_color)
                    self.assertEqual(payload["plot_bgcolor"], target.background_color)
                    self.assertEqual(payload["font.color"], target.text_color)
                    self.assertIn(target.muted_text_color, payload["title.text"])
                    self.assertEqual(payload["xaxis.gridcolor"], target.grid_color)
                    self.assertEqual(payload["xaxis.spikecolor"], target.grid_color)
                    self.assertEqual(payload["yaxis.gridcolor"], target.grid_color)
                    self.assertEqual(payload["legend.bgcolor"], target.overlay_background)
                    expected_content_colors = {
                        "shapes[0].line.color": target.change_color,
                        "shapes[1].line.color": target.pending_change_color,
                        "annotations[0].font.color": target.change_color,
                        "annotations[0].bgcolor": target.overlay_background,
                        "annotations[1].font.color": target.pending_change_color,
                        "annotations[1].bgcolor": target.overlay_background,
                    }
                    actual_content_colors = {
                        key: value for key, value in payload.items() if key.startswith(("shapes[", "annotations["))
                    }
                    self.assertEqual(actual_content_colors, expected_content_colors)
                    self.assertNotIn("shapes", payload)
                    self.assertNotIn("annotations", payload)
                    self.assertEqual(payload["updatemenus[0].bgcolor"], target.control_background)
                    self.assertEqual(payload["updatemenus[0].font.color"], target.control_text_color)
                    self.assertEqual(payload["updatemenus[0].bordercolor"], target.control_border_color)

    def test_theme_toggle_payloads_are_resilient_to_figure_content(self):
        # Given: empty, observation-only, model-only, and break figures.
        day_ms = 24 * 60 * 60 * 1000
        spec = PlotSpec(dataset="Landsat", band="B4", longitude=0.0, latitude=0.0)
        model = {
            "tStart": [[0.0]],
            "tEnd": [[10.0 * day_ms]],
            "tBreak": [[0.0]],
            "B4_coefs": [[[1.0] * 8]],
        }
        cases = {
            "empty": ({}, {"time": [], "B4": []}),
            "observations": ({}, {"time": [0.0], "B4": [1.0]}),
            "model": (model, {"time": [], "B4": []}),
            "break": (
                model | {"tBreak": [[5.0 * day_ms]], "changeProb": [[1.0]]},
                {"time": [], "B4": []},
            ),
        }

        # When/Then: each content shape builds two complete, trace-aligned payloads.
        for name, (result_info, timeseries) in cases.items():
            with self.subTest(name=name):
                figure = build_figure(result_info, timeseries, spec)
                self.assertEqual(len(figure.layout.updatemenus[0].buttons), 2)
                for button, target in zip(
                    figure.layout.updatemenus[0].buttons,
                    (LIGHT_THEME, DARK_THEME),
                    strict=True,
                ):
                    self.assertEqual(len(button.args[0]["line.color"]), len(figure.data))
                    payload = button.args[1]
                    expected_content_colors = {
                        f"shapes[{index}].line.color": (
                            target.change_color if shape.name == "Change" else target.pending_change_color
                        )
                        for index, shape in enumerate(figure.layout.shapes)
                    }
                    for index, _annotation in enumerate(figure.layout.annotations):
                        if index < len(figure.layout.shapes):
                            shape = figure.layout.shapes[index]
                            expected_content_colors[f"annotations[{index}].font.color"] = (
                                target.change_color if shape.name == "Change" else target.pending_change_color
                            )
                            expected_content_colors[f"annotations[{index}].bgcolor"] = target.overlay_background
                        else:
                            expected_content_colors[f"annotations[{index}].font.color"] = target.text_color
                    actual_content_colors = {
                        key: value for key, value in payload.items() if key.startswith(("shapes[", "annotations["))
                    }
                    self.assertEqual(actual_content_colors, expected_content_colors)
                    self.assertNotIn("shapes", payload)
                    self.assertNotIn("annotations", payload)

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
