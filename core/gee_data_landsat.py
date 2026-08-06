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

with the collaboration of Paulo Arevalo Orduz <parevalo@bu.edu>
https://github.com/parevalo/gee-ccdc-tools

Only Landsat Collection 2 is supported: USGS decommissioned Collection 1 and the
C01 datasets were removed from the Google Earth Engine data catalog.

"""

from dataclasses import dataclass
from typing import Final

from .gee_common import INDEX_BANDS, OPTICAL_BANDS, add_indices, filter_collection

# Collection 2 Level-2 scaling (USGS): SR = DN * 2.75e-5 - 0.2 over the valid DN range
# 7273-43636, which maps exactly onto surface reflectance [0.0, 1.0].
SR_SCALE: Final = 0.0000275
SR_OFFSET: Final = -0.2
# Reflectance below 0 is a legitimate retrieval artefact over dark targets (water, shadow), so a
# small negative tolerance is kept rather than dropping those observations. Above 1.0 the retrieval
# is outside the documented valid range and is dominated by residual thin cloud that CFmask missed;
# those pixels are high-magnitude outliers that would drive false CCDC breaks, so they are rejected.
SR_MIN: Final = -0.05
SR_MAX: Final = 1.0

# QA_PIXEL (CFmask) bits rejected: 0 fill, 1 dilated cloud, 2 cirrus (OLI only), 3 cloud,
# 4 cloud shadow, 5 snow. The cloud/shadow/snow *confidence* bits (8-15) are deliberately not
# used: requiring high confidence everywhere discards most low/medium-confidence clear pixels,
# and residual clouds are caught downstream by the CCDC TMask.
QA_BITS_TM: Final = (0, 1, 3, 4, 5)
QA_BITS_OLI: Final = (0, 1, 2, 3, 4, 5)

# Atmospheric opacity > 0.3 (scale factor 0.001) marks a hazy retrieval; TM/ETM+ only.
ATMOS_OPACITY_MAX: Final = 300
# SR_QA_AEROSOL bits 6-7 hold the aerosol level; only "high" (3) is rejected, so climatology,
# low and medium aerosol observations are kept. OLI/OLI-2 only.
AEROSOL_LEVEL_MASK: Final = 0b11000000
AEROSOL_LEVEL_HIGH: Final = 0b11000000

# Tasseled cap coefficients ordered as OPTICAL_BANDS (Blue, Green, Red, NIR, SWIR1, SWIR2).
#
# TM/ETM+ - Crist, E.P. & Cicone, R.C. (1984). A physically-based transformation of Thematic
# Mapper data - the TM Tasseled Cap. IEEE TGRS, 22(3), 256-263.
TC_TM: Final = {
    "BRIGHTNESS": [0.3037, 0.2793, 0.4743, 0.5585, 0.5082, 0.1863],
    "GREENNESS": [-0.2848, -0.2435, -0.5436, 0.7243, 0.0840, -0.1800],
    "WETNESS": [0.1509, 0.1973, 0.3279, 0.3406, -0.7112, -0.4572],
}
# OLI/OLI-2 - Baig, M.H.A., Zhang, L., Shuai, T. & Tong, Q. (2014). Derivation of a tasselled cap
# transformation based on Landsat 8 at-satellite reflectance. Remote Sensing Letters, 5(5), 423-431.
TC_OLI: Final = {
    "BRIGHTNESS": [0.3029, 0.2786, 0.4733, 0.5599, 0.508, 0.1872],
    "GREENNESS": [-0.2941, -0.243, -0.5424, 0.7276, 0.0713, -0.1608],
    "WETNESS": [0.1511, 0.1973, 0.3283, 0.3407, -0.7117, -0.4559],
}
# ETM+ deliberately reuses TC_TM rather than the Huang et al. (2002) ETM+ set. Huang's
# coefficients live in a differently normalised space, so for identical reflectance they return a
# Wetness up to 0.15 and a Greenness up to 0.07 away from what TC_TM and TC_OLI return - and those
# two agree with each other to within 0.001. Because L7 scenes are interleaved with L5/L8 scenes
# rather than following them, mixing the sets does not produce one step at a sensor handover: it
# splits the series into two clouds of points, inflating the segment RMSE and manufacturing breaks
# whenever BRIGHTNESS/GREENNESS/WETNESS is used as a breakpoint band.


@dataclass(frozen=True, slots=True)
class SensorSpec:
    """Per-sensor differences between the Collection 2 Level-2 products."""

    collection: str
    optical_bands: tuple[str, ...]
    qa_pixel_bits: tuple[int, ...]
    # QA_RADSAT bits for the optical bands actually used; bit N flags saturation of band N+1.
    saturation_mask: int
    # TM/ETM+ carry SR_ATMOS_OPACITY, OLI/OLI-2 carry SR_QA_AEROSOL; the two encode haze differently
    aerosol_band: str
    tc_coefficients: dict


# QA_RADSAT: TM/ETM+ use B1-B5 (bits 0-4) and B7 (bit 6); OLI uses B2-B7 (bits 1-6).
TM_BANDS: Final = ("SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7")
OLI_BANDS: Final = ("SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7")

SENSORS: Final = (
    SensorSpec("LANDSAT/LT04/C02/T1_L2", TM_BANDS, QA_BITS_TM, 0b01011111, "SR_ATMOS_OPACITY", TC_TM),
    SensorSpec("LANDSAT/LT05/C02/T1_L2", TM_BANDS, QA_BITS_TM, 0b01011111, "SR_ATMOS_OPACITY", TC_TM),
    SensorSpec("LANDSAT/LE07/C02/T1_L2", TM_BANDS, QA_BITS_TM, 0b01011111, "SR_ATMOS_OPACITY", TC_TM),
    SensorSpec("LANDSAT/LC08/C02/T1_L2", OLI_BANDS, QA_BITS_OLI, 0b01111110, "SR_QA_AEROSOL", TC_OLI),
    SensorSpec("LANDSAT/LC09/C02/T1_L2", OLI_BANDS, QA_BITS_OLI, 0b01111110, "SR_QA_AEROSOL", TC_OLI),
)


def _haze_mask(image, spec):
    """Reject hazy retrievals using whichever aerosol product the sensor carries."""
    aerosol = image.select(spec.aerosol_band)
    if spec.aerosol_band == "SR_QA_AEROSOL":
        return aerosol.bitwiseAnd(AEROSOL_LEVEL_MASK).neq(AEROSOL_LEVEL_HIGH)
    # unmask() fills pixels with no opacity retrieval with 0, i.e. treats them as haze-free rather
    # than dropping the observation.
    return aerosol.unmask().lt(ATMOS_OPACITY_MAX)


def prepare_image(image, spec):
    """Scale, rename and mask one Collection 2 Level-2 image into the common band schema."""
    import ee

    scaled = image.select(list(spec.optical_bands)).multiply(SR_SCALE).add(SR_OFFSET).rename(list(OPTICAL_BANDS))

    # ANDing "bit N is 0" over a set of bits is the same as testing the whole bitmask at once
    qa_bitmask = sum(1 << bit for bit in spec.qa_pixel_bits)
    clear = image.select("QA_PIXEL").bitwiseAnd(qa_bitmask).eq(0)
    no_saturation = image.select("QA_RADSAT").bitwiseAnd(spec.saturation_mask).eq(0)
    in_range = scaled.reduce(ee.Reducer.min()).gt(SR_MIN).And(scaled.reduce(ee.Reducer.max()).lte(SR_MAX))
    # The Landsat 7 SLC-off gaps themselves are already fill, which the QA_PIXEL bit 0 test above
    # rejects. Eroding one further pixel to drop the gap rims as well was measured 1.5-3.5x slower
    # over a 40-year series - a focal op has to fetch a neighbourhood for every ETM+ scene - which
    # is not worth it for the rim alone.
    mask = clear.And(no_saturation).And(in_range).And(_haze_mask(image, spec))

    # Arithmetic operations do not carry image properties across, and system:time_start is what
    # sort(), CCDC and getRegion need.
    #
    # Only that one. Copying image.propertyNames() instead carries ~98 properties per scene -
    # including system:footprint, a geometry - through the merge and the sort, and measured 2.5x
    # slower end to end on a 40-year series as well as exhausting the Earth Engine memory limit on
    # dense points. Nothing downstream reads the scene metadata, so it is pure cost.
    return ee.Image(scaled.updateMask(mask).copyProperties(image, ["system:time_start"]))


def get_gee_data_landsat(coords, date_range, doy_range, indices=INDEX_BANDS):
    """Filtered, masked and index-augmented Landsat Collection 2 series at a point.

    `indices` limits which spectral indices are computed; see gee_common.add_indices.
    """
    import ee

    point = ee.Geometry.Point(coords)

    def build(spec):
        return filter_collection(spec.collection, point, date_range, doy_range).map(
            lambda image: add_indices(prepare_image(image, spec), spec.tc_coefficients, indices)
        )

    collections = [build(spec) for spec in SENSORS]
    merged = collections[0]
    for collection in collections[1:]:
        merged = merged.merge(collection)

    # CCDC and the plot both need the series in chronological order
    return merged.sort("system:time_start")
