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

Version checks for the extra libraries, kept free of QGIS imports so they can be tested anywhere.
"""

import re
from typing import Final

# The plot puts the break lines in the legend through layout shapes with legendgroup/showlegend,
# which plotly added in 5.16: on 5.15 building the figure fails with "Invalid property".
MIN_PLOTLY_VERSION: Final = (5, 16)


def version_tuple(text) -> tuple[int, ...]:
    """The numeric release part of a version string: "5.16.0" -> (5, 16, 0), "6.0.0rc1" -> (6, 0, 0)."""
    parts = []
    for piece in str(text).split("."):
        digits = re.match(r"\d+", piece)
        if digits is None:
            break
        parts.append(int(digits.group()))
        if digits.group() != piece:
            break
    return tuple(parts)


def version_satisfies(text, minimum) -> bool:
    return version_tuple(text) >= tuple(minimum)
