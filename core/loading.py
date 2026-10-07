from typing import assert_never

from .plot import DARK_THEME, LIGHT_THEME, PlotStyle, PlotTheme


def _theme(style: PlotStyle) -> PlotTheme:
    match style:
        case PlotStyle.LIGHT:
            return LIGHT_THEME
        case PlotStyle.DARK:
            return DARK_THEME
        case unreachable:
            assert_never(unreachable)


def blank_page_html(style: PlotStyle) -> str:
    """The empty view, in the plot's background, for when there is no plot to show."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CCD</title>
<style>
html, body {{
    width: 100%;
    height: 100%;
    margin: 0;
    background-color: {_theme(style).background_color};
}}
</style>
</head>
<body></body>
</html>"""


def loading_page_html(style: PlotStyle) -> str:
    theme = _theme(style)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Loading</title>
<style>
html, body {{
    width: 100%;
    height: 100%;
    margin: 0;
    background-color: {theme.background_color};
}}
.spinner {{
    position: absolute;
    top: 50%;
    left: 50%;
    width: 48px;
    height: 48px;
    border: 6px solid {theme.grid_color};
    border-top-color: {theme.text_color};
    border-radius: 50%;
    transform: translate(-50%, -50%);
    animation: spin 0.8s linear infinite;
}}
@keyframes spin {{
    from {{ transform: translate(-50%, -50%) rotate(0deg); }}
    to {{ transform: translate(-50%, -50%) rotate(360deg); }}
}}
</style>
</head>
<body><div class="spinner" role="status" aria-label="Loading"></div></body>
</html>"""
