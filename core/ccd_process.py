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
import numpy as np
from datetime import datetime

import ccd
from CCD_Plugin.core.gee_data import get_full_collection, get_data_full


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


def compute_ccd(coords, date_range, doy_range, collection, band):

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
    data_collection = get_full_collection(coords, date_range, doy_range, collection)
    data_point = get_data_full(data_collection, coords)[1::]

    # generate a merge/fusion mask layer of nan/none values to filter all data
    nan_masks = [[0 if dp[i] is None else 1 for dp in data_point] for i in range(3, 12)]
    # fusion masks
    nan_mask = [0 if 0 in m else 1 for m in zip(*nan_masks)]

    # get each features applying the mask
    dates, blues, greens, reds, nirs, swir1s, swir2s, thermals, qas = \
        mask([dp[3] for dp in data_point], nan_mask), mask([dp[4] for dp in data_point], nan_mask), \
        mask([dp[5] for dp in data_point], nan_mask), mask([dp[6] for dp in data_point], nan_mask), \
        mask([dp[7] for dp in data_point], nan_mask), mask([dp[8] for dp in data_point], nan_mask), \
        mask([dp[9] for dp in data_point], nan_mask), mask([dp[10] for dp in data_point], nan_mask), \
        mask([dp[11] for dp in data_point], nan_mask)

    # convert the dates from miliseconds unix time to ordinal
    dates = np.array([datetime.fromtimestamp(int(str(int(d))[:-3])).toordinal() for d in dates])

    results = ccd.detect(dates, blues, greens, reds, nirs, swir1s, swir2s, thermals, qas)

    # get the results by band
    band_name = {"Blue": blues, "Green": greens, "Red": reds, "NIR": nirs, "SWIR1": swir1s, "SWIR2": swir2s}
    band_data = np.array(band_name[band])

    return results, dates, band_data
