from typing import assert_never

from .plot import DARK_THEME, LIGHT_THEME, PlotStyle


def loading_page_html(style: PlotStyle) -> str:
    match style:
        case PlotStyle.LIGHT:
            theme = LIGHT_THEME
        case PlotStyle.DARK:
            theme = DARK_THEME
        case unreachable:
            assert_never(unreachable)

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
    -webkit-transform: translate(-50%, -50%);
    transform: translate(-50%, -50%);
    -webkit-animation: spin 0.8s linear infinite;
    animation: spin 0.8s linear infinite;
}}
@-webkit-keyframes spin {{
    from {{ -webkit-transform: translate(-50%, -50%) rotate(0deg); }}
    to {{ -webkit-transform: translate(-50%, -50%) rotate(360deg); }}
}}
@keyframes spin {{
    from {{ transform: translate(-50%, -50%) rotate(0deg); }}
    to {{ transform: translate(-50%, -50%) rotate(360deg); }}
}}
</style>
</head>
<body><div class="spinner" role="status" aria-label="Loading"></div></body>
</html>"""
