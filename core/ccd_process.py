# -*- coding: utf-8 -*-
"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019-2022 by Xavier Corredor Llano, SMByC
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
import ccd
import numpy as np
from datetime import datetime
from qgis.core import Qgis

from CCD_Plugin.core.gee_data import get_gee_data_landsat


def mask(input_list, boolean_mask):
    """Apply boolean mask to input list

    Args:
        input_list (list): Input list for apply mask
        boolean_mask (list): The boolean mask list

    Examples:
        >>> mask(['A','B','C','D'], [1,0,1,0])
        ['A', 'C']
    """
    return [i for i, b in zip(input_list, boolean_mask) if b]

ccd_results = {}

def compute_ccd(coords, date_range, doy_range, collection, band_or_index):

    # get data from Google Earth Engine
    # list index order:
    # 'longitude', 1
    # 'latitude',2
    # 'time',3
    # 'BLUE',4
    # 'GREEN',5
    # 'RED',6
    # 'NIR',7
    # 'SWIR1',8
    # 'SWIR2',9
    # 'THERMAL',10
    # 'pixel_qa',11

    ### get GEE data from the specific point
    gee_data_point = get_gee_data_landsat(coords, date_range, doy_range, collection)

    # generate a merge/fusion mask layer of nan/none values to filter all data
    nan_masks = [[0 if dp[i] is None else 1 for dp in gee_data_point] for i in range(3, 12)]
    # fusion masks
    nan_mask = [0 if 0 in m else 1 for m in zip(*nan_masks)]

    # get each features applying the mask
    dates, blues, greens, reds, nirs, swir1s, swir2s, thermals, qas = \
        [mask([dp[i] for dp in gee_data_point], nan_mask) for i in range(3, 12)]

    # mask the indices range 12-19 for 'NBR', 'NDVI', 'EVI', 'EVI2', 'BRIGHTNESS', 'GREENNESS', 'WETNESS'
    nbrs, ndvis, evis, evi2s, brightnesss, greennesss, wetnesss = \
        [mask([dp[i] for dp in gee_data_point], nan_mask) for i in range(12, 19)]

    # # multiply by 10000 to nbrs, ndvis, evis, evi2s
    nbrs, ndvis, evis, evi2s = \
        [np.array([i * 10000 for i in b]) for b in [nbrs, ndvis, evis, evi2s]]

    # convert the dates from miliseconds unix time to ordinal
    dates = np.array([datetime.fromtimestamp(int(str(int(d))[:-3])).toordinal() for d in dates])

    # check if nan_mask is all zeros, not clean data available
    if not any(nan_mask):
        from CCD_Plugin.CCD_Plugin import CCD_Plugin
        CCD_Plugin.widget.MsgBar.pushMessage("Error: Not enough clean data to compute CCD for this point",
                                             level=Qgis.Warning, duration=5)
        return

    results = ccd.detect(dates, blues, greens, reds, nirs, swir1s, swir2s, thermals, nbrs, ndvis, evis, evi2s, brightnesss, greennesss, wetnesss, qas)

    ts_by_band_or_index = {"Blue": blues, "Green": greens, "Red": reds, "NIR": nirs, "SWIR1": swir1s, "SWIR2": swir2s,
                        "NBR": nbrs, "NDVI": ndvis, "EVI": evis, "EVI2": evi2s, "BRIGHTNESS": brightnesss,
                        "GREENNESS": greennesss, "WETNESS": wetnesss}

    time_series = np.array(ts_by_band_or_index[band_or_index])

    # store the results
    global ccd_results
    ccd_results = {(coords, date_range, doy_range, collection): (results, dates, ts_by_band_or_index)}

    return results, dates, time_series
