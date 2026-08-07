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

with the collaboration of:
    Paulo Arevalo Orduz <parevalo@bu.edu>
    Daniel Moraes <moraesd90@gmail.com>
"""

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final, TypeAlias, assert_never

import plotly.graph_objects as go

from .gee_common import INDEX_BANDS, OPTICAL_BANDS
from .plot_data import MILLISECONDS_PER_YEAR as MILLISECONDS_PER_YEAR
from .plot_data import ModelSegment as ModelSegment
from .plot_data import build_model_segments as build_model_segments
from .plot_data import evaluate_ccdc_model as evaluate_ccdc_model
from .plot_data import normalize_observations as normalize_observations
from .plot_data import sample_segment_dates as sample_segment_dates

_PlotlyValue: TypeAlias = (  # noqa: UP040
    str
    | int
    | float
    | bool
    | datetime
    | list["_PlotlyValue"]
    | tuple["_PlotlyValue", ...]
    | Mapping[str, "_PlotlyValue"]
    | None
)
_ThemeLayoutPayload: TypeAlias = dict[str, _PlotlyValue]  # noqa: UP040

OBSERVATION_COLOR: Final = "#3F83B5"
# Each CCDC segment is a separate model fit, so each gets its own colour: consecutive segments
# then read apart at a glance instead of looking like one broken line. The colour carries no
# meaning of its own - the legend is a single neutral entry - so it is free to cycle.
#
# Stepped in OKLCH and validated as opaque base colours (light surface #FFFFFF, adjacent pairs):
# lightness band, chroma floor, normal-vision floor (20.3) and 3:1 contrast all pass. A paler step
# than this drops the green and olive under 3:1 even before any transparency, which is why the
# softening below is done with opacity rather than by lightening these further.
# Lightness alternates as well as hue, which is what keeps consecutive segments apart for
# colour-blind readers, where hue alone collapses.
#
# The blue/magenta pair sits in the 6-8 CVD band (7.5 protan). That is allowed here because the
# colour is non-semantic and the segments carry the secondary encoding the band requires: they are
# disjoint in time, separated by a break line or a data gap, and never touch. The pair is also
# slots 4 and 5, so it only ever appears on a pixel with five fitted segments.
MODEL_COLORS: Final = ("#50a064", "#8b66b3", "#979836", "#1b8abd", "#bb6690")
MODEL_LINE_WIDTH: Final = 2
# Drawn semi-transparent so the fit reads as a soft overlay on its own observations rather than a
# band painted across them. Composited over white this lands the slots at 2.4-2.9:1, under the 3:1
# a solid data mark would want - a deliberate trade for the softer look. It only bites in the gaps
# between observations; over the scatter the line blends with the points rather than the surface,
# and the fit stays reachable through its hover tooltip.
MODEL_LINE_OPACITY: Final = 0.8
# The legend stands for "a CCDC fit" in general, not for any one segment, so it is neutral
MODEL_LEGEND_COLOR: Final = "#8A8F98"
CHANGE_COLOR: Final = "#C45C5C"
PENDING_CHANGE_COLOR: Final = "#D9A566"
GRID_COLOR: Final = "#E4EAF0"
TEXT_COLOR: Final = "#334155"
MUTED_TEXT_COLOR: Final = "#64748B"
BACKGROUND_COLOR: Final = "#FFFFFF"
# The legend and the break labels sit inside the plot area, so they need to stay readable over
# the scatter without hiding it
OVERLAY_BACKGROUND: Final = "rgba(255, 255, 255, 0.72)"
SYSTEM_FONT: Final = 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
X_MARGIN_RATIO: Final = 0.02
SINGLE_DATE_MARGIN: Final = timedelta(days=30)
SURFACE_REFLECTANCE_BANDS: Final = frozenset(OPTICAL_BANDS)
SPECTRAL_INDEX_BANDS: Final = frozenset(INDEX_BANDS)


class PlotStyle(Enum):
    LIGHT = "light"
    DARK = "dark"


def resolve_plot_style(value: str | None, fallback: PlotStyle) -> PlotStyle:
    return fallback if value is None else PlotStyle(value)


@dataclass(frozen=True, slots=True)
class PlotTheme:
    observation_color: str
    model_colors: tuple[str, ...]
    model_legend_color: str
    change_color: str
    pending_change_color: str
    grid_color: str
    text_color: str
    muted_text_color: str
    background_color: str
    overlay_background: str
    control_background: str
    control_text_color: str
    control_border_color: str
    control_active_background: str
    control_hover_background: str


LIGHT_THEME: Final = PlotTheme(
    observation_color=OBSERVATION_COLOR,
    model_colors=MODEL_COLORS,
    model_legend_color=MODEL_LEGEND_COLOR,
    change_color=CHANGE_COLOR,
    pending_change_color=PENDING_CHANGE_COLOR,
    grid_color=GRID_COLOR,
    text_color=TEXT_COLOR,
    muted_text_color=MUTED_TEXT_COLOR,
    background_color=BACKGROUND_COLOR,
    overlay_background=OVERLAY_BACKGROUND,
    control_background=OVERLAY_BACKGROUND,
    control_text_color=TEXT_COLOR,
    control_border_color=GRID_COLOR,
    control_active_background="#F4FAFF",
    control_hover_background="#F4FAFF",
)
DARK_THEME: Final = PlotTheme(
    observation_color="#68B4E3",
    model_colors=("#65C47A", "#B68BDD", "#C4C55A", "#45B4E8", "#DE7BA5"),
    model_legend_color="#B5BEC8",
    change_color="#FF8585",
    pending_change_color="#F0B965",
    grid_color="#4C5864",
    text_color="#E6EDF3",
    muted_text_color="#AAB6C2",
    background_color="#20252B",
    overlay_background="rgba(32, 37, 43, 0.88)",
    control_background="#2C333A",
    control_text_color="#E6EDF3",
    control_border_color="#4C5864",
    control_active_background="#3B4652",
    control_hover_background="#46535E",
)


@dataclass(frozen=True, slots=True)
class PlotSpec:
    dataset: str
    band: str
    longitude: float
    latitude: float


def _utc_datetime(timestamp_ms: float) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC)


def _y_axis_title(spec: PlotSpec) -> str:
    """Just the quantity: the band and dataset are named in the title.

    The axis title is rendered rotated, so its length costs plot *height*. In a short dock a
    "Surface reflectance - SWIR1 (Landsat C2)" title is taller than the plot area and gets clipped
    at both ends.
    """
    if spec.band in SURFACE_REFLECTANCE_BANDS:
        return "Surface reflectance"
    if spec.band in SPECTRAL_INDEX_BANDS:
        return "Index"
    return "Value"


def _theme_settings(style: PlotStyle) -> tuple[PlotTheme, int]:
    match style:
        case PlotStyle.LIGHT:
            return LIGHT_THEME, 0
        case PlotStyle.DARK:
            return DARK_THEME, 1
        case unreachable:
            assert_never(unreachable)


def _page_theme_script(style: PlotStyle) -> str:
    theme, _ = _theme_settings(style)
    return f"""
const graphDiv = document.getElementById("{{plot_id}}");
const backgrounds = Object.freeze({{
    "Light": "{LIGHT_THEME.background_color}",
    "Dark": "{DARK_THEME.background_color}"
}});
const horizontalPadding = 7;
const minimumWidth = 30;
const buttonGap = 2;
const visibleHeight = 20;
const textBaselineY = 13;
const setPageBackground = (color) => {{
    document.documentElement.style.backgroundColor = color;
    document.body.style.backgroundColor = color;
}};
const currentActiveIndex = () => graphDiv._fullLayout.updatemenus[0].active;
const ensureThemeControlStyles = () => {{
    graphDiv.classList.add("ccd-theme-controls");
    if (!graphDiv.querySelector('style[data-ccd-theme-controls="true"]')) {{
        const style = document.createElement("style");
        style.dataset.ccdThemeControls = "true";
        style.textContent = `
.ccd-theme-controls.ccd-theme-dark .updatemenu-item-rect {{
    fill: {DARK_THEME.control_background} !important;
}}
.ccd-theme-controls.ccd-theme-dark .updatemenu-button.ccd-theme-active .updatemenu-item-rect {{
    fill: {DARK_THEME.control_active_background} !important;
}}
.ccd-theme-controls.ccd-theme-dark .updatemenu-button:hover .updatemenu-item-rect {{
    fill: {DARK_THEME.control_hover_background} !important;
}}
`;
        graphDiv.appendChild(style);
    }}
}};
const adjustThemeControls = () => {{
    const buttons = Array.from(graphDiv.querySelectorAll(".updatemenu-button"));
    const rects = Array.from(graphDiv.querySelectorAll(".updatemenu-item-rect"));
    const texts = Array.from(graphDiv.querySelectorAll(".updatemenu-item-text"));
    if (!buttons.length || buttons.length !== rects.length || buttons.length !== texts.length) {{
        return;
    }}
    const activeIndex = currentActiveIndex();
    graphDiv.classList.toggle("ccd-theme-dark", activeIndex === 1);
    const imageCornerX = 0;
    const imageCornerY = 0;
    let currentX = imageCornerX;
    const rowY = imageCornerY;
    buttons.forEach((button, index) => {{
        const rect = rects[index];
        const text = texts[index];
        const width = Math.max(minimumWidth, text.getBBox().width + 2 * horizontalPadding);
        button.setAttribute("transform", `translate(${{currentX}},${{rowY}})`);
        rect.setAttribute("width", width);
        rect.setAttribute("height", visibleHeight);
        text.setAttribute("x", horizontalPadding);
        text.setAttribute("y", textBaselineY);
        currentX += width + buttonGap;
        button.classList.toggle("ccd-theme-active", index === activeIndex);
    }});
}};
let adjustmentFrame = 0;
const scheduleThemeControlAdjustment = () => {{
    if (adjustmentFrame) {{
        cancelAnimationFrame(adjustmentFrame);
    }}
    adjustmentFrame = requestAnimationFrame(() => {{
        adjustmentFrame = 0;
        adjustThemeControls();
    }});
}};
ensureThemeControlStyles();
setPageBackground("{theme.background_color}");
graphDiv.on("plotly_buttonclicked", (event) => {{
    setPageBackground(backgrounds[event.button.label]);
    window.location.hash = event.button.label.toLowerCase();
    scheduleThemeControlAdjustment();
}});
graphDiv.on("plotly_afterplot", scheduleThemeControlAdjustment);
scheduleThemeControlAdjustment();
"""


def _trace_colors(theme: PlotTheme, has_observations: bool, segment_count: int) -> list[str]:
    colors = [theme.observation_color] if has_observations else []
    if segment_count:
        colors.append(theme.model_legend_color)
        colors.extend(theme.model_colors[index % len(theme.model_colors)] for index in range(segment_count))
    return colors


def _theme_layout_payload(figure: go.Figure, source: PlotTheme, target: PlotTheme) -> _ThemeLayoutPayload:
    payload: _ThemeLayoutPayload = {
        "paper_bgcolor": target.background_color,
        "plot_bgcolor": target.background_color,
        "font.color": target.text_color,
        "title.text": figure.layout.title.text.replace(source.muted_text_color, target.muted_text_color),
        "title.font.color": target.text_color,
        "xaxis.gridcolor": target.grid_color,
        "xaxis.spikecolor": target.grid_color,
        "xaxis.tickfont.color": target.text_color,
        "xaxis.title.font.color": target.text_color,
        "yaxis.gridcolor": target.grid_color,
        "yaxis.tickfont.color": target.text_color,
        "yaxis.title.font.color": target.text_color,
        "legend.bgcolor": target.overlay_background,
        "legend.font.color": target.text_color,
        "updatemenus[0].bgcolor": target.control_background,
        "updatemenus[0].bordercolor": target.control_border_color,
        "updatemenus[0].font.color": target.control_text_color,
    }
    for index, shape in enumerate(figure.layout.shapes):
        payload[f"shapes[{index}].line.color"] = (
            target.change_color if shape.name == "Change" else target.pending_change_color
        )
    for index, _annotation in enumerate(figure.layout.annotations):
        if index < len(figure.layout.shapes):
            shape = figure.layout.shapes[index]
            payload[f"annotations[{index}].font.color"] = (
                target.change_color if shape.name == "Change" else target.pending_change_color
            )
            payload[f"annotations[{index}].bgcolor"] = target.overlay_background
        else:
            payload[f"annotations[{index}].font.color"] = target.text_color
    return payload


def build_figure(ccdc_result_info, timeseries, spec: PlotSpec, *, style: PlotStyle = PlotStyle.LIGHT) -> go.Figure:
    theme, active_style_index = _theme_settings(style)
    observation_times, observation_values = normalize_observations(timeseries, spec.band)
    segments = build_model_segments(ccdc_result_info, spec.band)
    figure = go.Figure()

    if observation_times.size:
        figure.add_trace(
            go.Scatter(
                x=[_utc_datetime(timestamp_ms) for timestamp_ms in observation_times],
                y=observation_values,
                name="Observed",
                mode="markers",
                marker={"color": theme.observation_color, "size": 4.5, "opacity": 0.72},
                hovertemplate="Date %{x|%Y-%m-%d}<br>Value %{y:.4f}<extra></extra>",
            )
        )

    if segments:
        # One neutral legend entry standing for every segment. Plotly takes a legend swatch from
        # its trace, so a per-segment entry would repeat "CCDC fit" in five colours; an empty
        # proxy trace in the same legendgroup gives one entry that still toggles them all.
        figure.add_trace(
            go.Scatter(
                # a single null point, not an empty trace: plotly.js drops legend entries for
                # traces with no points at all, and this one draws nothing either way
                x=[None],
                y=[None],
                name="CCDC fit",
                mode="lines",
                legendgroup="model",
                showlegend=True,
                line={"color": theme.model_legend_color, "width": MODEL_LINE_WIDTH},
                opacity=MODEL_LINE_OPACITY,
                hoverinfo="skip",
            )
        )

    for index, segment in enumerate(segments):
        start = _utc_datetime(segment.start_ms)
        end = _utc_datetime(segment.end_ms)
        # Only what the plot itself does not already say. Every break is drawn as its own line
        # labelled with the date, and an unconfirmed one carries its percentage there too, so
        # repeating either here would just make the tooltip longer.
        details = [f"Start {start:%Y-%m-%d}", f"End {end:%Y-%m-%d}"]
        if segment.rmse is not None:
            details.append(f"RMSE {segment.rmse:.4f}")
        figure.add_trace(
            go.Scatter(
                x=[_utc_datetime(timestamp_ms) for timestamp_ms in segment.dates_ms],
                y=segment.values,
                name="CCDC fit",
                mode="lines",
                legendgroup="model",
                showlegend=False,
                line={"color": theme.model_colors[index % len(theme.model_colors)], "width": MODEL_LINE_WIDTH},
                opacity=MODEL_LINE_OPACITY,
                hovertemplate=(
                    f"Segment {segment.number}<br>Model value %{{y:.4f}}<br>" + "<br>".join(details) + "<extra></extra>"
                ),
            )
        )

    # A segment can carry a tBreak that CCDC never confirmed: while a change accumulates the
    # consecutive observations minObservations requires, changeProb sits between 0 and 1, and it
    # stays there if the series ends first. Drawing those the same as a confirmed break reports a
    # change that CCDC did not detect, most often right at the end of the series.
    break_count = 0
    pending_count = 0
    for segment in segments:
        if segment.break_ms is None:
            continue
        confirmed = segment.is_confirmed_break
        break_date = _utc_datetime(segment.break_ms)
        if confirmed:
            color, dash, width = theme.change_color, "dash", 1
            label, group = "Change", "change"
            first = break_count == 0
            break_count += 1
        else:
            color, dash, width = theme.pending_change_color, "dot", 1
            label, group = "In progress", "pending"
            first = pending_count == 0
            pending_count += 1
        figure.add_shape(
            type="line",
            x0=break_date,
            x1=break_date,
            y0=0,
            y1=1,
            xref="x",
            yref="paper",
            line={"color": color, "width": width, "dash": dash},
            name=label,
            legendgroup=group,
            showlegend=first,
        )
        # the exact break date is the plugin's main output, so label it in full rather than by year
        text = f"{break_date:%Y-%m-%d}"
        if not confirmed:
            text += f" ({segment.change_probability:.0%})"
        figure.add_annotation(
            x=break_date,
            y=0.02,
            xref="x",
            yref="paper",
            text=text,
            showarrow=False,
            textangle=-90,
            xanchor="right" if (break_count + pending_count) % 2 == 1 else "left",
            yanchor="bottom",
            font={"color": color, "size": 9},
            # the label lands on top of the scatter, so back it just enough to stay legible
            bgcolor=theme.overlay_background,
            borderpad=1,
        )

    all_dates = [*observation_times]
    for segment in segments:
        all_dates.extend((segment.start_ms, segment.end_ms))
    x_axis = {
        "automargin": True,
        "fixedrange": False,
        "gridcolor": theme.grid_color,
        "nticks": 9,
        "showspikes": True,
        "spikecolor": theme.grid_color,
        "spikemode": "across",
        "spikethickness": 1,
        # years read fine horizontally and a slanted label costs plot height for no gain
        "tickangle": 0,
        "tickformat": "%Y",
        "title_text": None,
        "zeroline": False,
    }
    if all_dates:
        earliest, latest = min(all_dates), max(all_dates)
        margin_ms = (latest - earliest) * X_MARGIN_RATIO
        if earliest == latest:
            margin_ms = SINGLE_DATE_MARGIN.total_seconds() * 1000
        x_axis["range"] = [_utc_datetime(earliest - margin_ms), _utc_datetime(latest + margin_ms)]

    if not observation_times.size and not segments:
        figure.add_annotation(
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            text="No valid observations or fitted model segments",
            showarrow=False,
            font={"color": theme.text_color, "size": 12},
        )

    figure.update_layout(
        autosize=True,
        hovermode="closest",
        paper_bgcolor=theme.background_color,
        plot_bgcolor=theme.background_color,
        font={"color": theme.text_color, "family": SYSTEM_FONT, "size": 11},
        # What the series is on the first line, where it came from on a smaller, muted second one.
        # Two lines via <br> rather than the native title.subtitle, which has no gap control and
        # leaves them further apart than they need to be. Anchored bottom-to-the-plot so the block
        # grows up into the reserved margin: anchoring it to the top is what clipped the first line
        # off the figure, since plotly positions a multi-line title block by its first line.
        title={
            "text": (
                f"{spec.band} · {spec.dataset}"
                f"<br><span style='font-size:11px;color:{theme.muted_text_color}'>"
                f"Lat: {spec.latitude:.5f}  Lon: {spec.longitude:.5f}"
                f"  ·  {observation_values.size} obs"
                f"  ·  {len(segments)} segment{'' if len(segments) == 1 else 's'}"
                f"  ·  {break_count} break{'' if break_count == 1 else 's'}"
                + (f"  ·  {pending_count} in progress" if pending_count else "")
                + "</span>"
            ),
            "x": 0.5,
            "xanchor": "center",
            "yref": "paper",
            "y": 1,
            "yanchor": "bottom",
            "pad": {"b": 18},
            "font": {"color": theme.text_color, "size": 13},
        },
        # Floated into the top-right of the plot area instead of stacked under the title, where it
        # was competing with it for the top margin: the title is anchored to the figure and the
        # legend to the plot area, so on a short dock the two collided.
        legend={
            "orientation": "h",
            "x": 1,
            "xanchor": "right",
            "y": 1,
            "yanchor": "top",
            "bgcolor": theme.overlay_background,
            "borderwidth": 0,
            "font": {"color": theme.text_color, "size": 10},
        },
        # Every margin at 75% of what it was, which is as tight as the header goes: plotly
        # positions the title block by its *first* line, so the top has to clear (t - title pad.b)
        # for line one plus the second line below it. At 60% the subtitle sits on the plot border.
        # Left and bottom are floors only - the axes carry automargin and grow past these to fit
        # their labels.
        margin={"l": 2, "r": 2, "b": 2, "t": 32, "pad": 0},
        xaxis=x_axis,
        yaxis={
            "automargin": True,
            "gridcolor": theme.grid_color,
            "title_text": _y_axis_title(spec),
            "zeroline": False,
        },
    )
    light_trace_colors = _trace_colors(LIGHT_THEME, bool(observation_times.size), len(segments))
    dark_trace_colors = _trace_colors(DARK_THEME, bool(observation_times.size), len(segments))
    figure.update_layout(
        updatemenus=[
            {
                "active": active_style_index,
                "bgcolor": theme.control_background,
                "bordercolor": theme.control_border_color,
                "buttons": [
                    {
                        "args": [
                            {"marker.color": light_trace_colors, "line.color": light_trace_colors},
                            _theme_layout_payload(figure, theme, LIGHT_THEME),
                        ],
                        "label": "Light",
                        "method": "update",
                    },
                    {
                        "args": [
                            {"marker.color": dark_trace_colors, "line.color": dark_trace_colors},
                            _theme_layout_payload(figure, theme, DARK_THEME),
                        ],
                        "label": "Dark",
                        "method": "update",
                    },
                ],
                "direction": "right",
                "font": {"color": theme.control_text_color, "size": 10},
                "showactive": True,
                "type": "buttons",
                "x": 0,
                "xanchor": "left",
                "y": 1,
                "yanchor": "bottom",
            }
        ]
    )
    return figure


def generate_plot(
    id, ccdc_result_info, timeseries, dataset, band_or_index_to_plot, *, style: PlotStyle = PlotStyle.LIGHT
):
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    plugin = CCD_Plugin.inst[id]
    spec = PlotSpec(
        dataset=dataset,
        band=band_or_index_to_plot,
        longitude=float(plugin.widget.longitude.value()),
        latitude=float(plugin.widget.latitude.value()),
    )
    figure = build_figure(ccdc_result_info, timeseries, spec, style=style)
    with tempfile.NamedTemporaryFile(suffix=".html", dir=plugin.tmp_dir, delete=False) as output:
        html_file = output.name
    figure.write_html(
        html_file,
        full_html=True,
        # "directory" drops plotly.min.js beside the page once per session instead of inlining
        # ~3.5 MB into every plot; the webview has LocalContentCanAccessFileUrls enabled and the
        # temp directory is removed on unload, so the sibling file resolves and is cleaned up.
        include_plotlyjs="directory",
        auto_open=False,
        post_script=_page_theme_script(style),
        config={
            "displaylogo": False,
            "responsive": True,
            "displayModeBar": "hover",
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
            "toImageButtonOptions": {
                "format": "png",
                "filename": f"ccd_{band_or_index_to_plot.lower().replace(' ', '_')}",
                "scale": 2,
            },
        },
    )
    return html_file
