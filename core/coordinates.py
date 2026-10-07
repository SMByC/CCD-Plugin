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

from typing import Final

# Decimal places the coordinate controls keep. 7 decimals is ~1 cm: with 5 (~1.1 m) a click within
# half a metre of a pixel edge was rounded into the neighbouring 10 m or 30 m pixel before it was
# sent to Earth Engine, while the marker stayed where the user clicked.
COORDINATE_DECIMALS: Final = 7


def normalize_longitude(longitude: float) -> float:
    """Wrap a longitude into [-180, 180).

    A geographic canvas panned past the antimeridian reports longitudes beyond 180, which the
    longitude control silently clamped to 180, moving the analysis to another place entirely.
    """
    return (longitude + 180.0) % 360.0 - 180.0
