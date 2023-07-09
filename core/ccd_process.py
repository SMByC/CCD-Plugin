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

with the collaboration of Daniel Moraes <moraesd90@gmail.com>

"""
import numpy as np
#from qgis.core import Qgis

from CCD_Plugin.core.gee_data_landsat import get_gee_data_landsat
from CCD_Plugin.core.gee_data_sentinel import getImageCollection

ccd_results = {}


def compute_ccd(coords, date_range, doy_range, dataset, breakpoint_bands, tmask, numObs, chi, minYears, lda):

    import ee
    point = ee.Geometry.Point(coords)

    ### determine gee scale (30m for LS / 10m for S2)
    gee_scale = 30 if dataset in ["Landsat C1", "Landsat C2"] else 10

    ### get GEE data from the specific point according to selected collection
    if dataset== "Sentinel-2":
        gee_data = getImageCollection(coords, date_range, doy_range, dataset) #cloud filter selection can be implemented later
    elif dataset== "Landsat C1":
        gee_data = get_gee_data_landsat(coords, date_range, doy_range, 1)
    elif dataset== "Landsat C2":
        gee_data = get_gee_data_landsat(coords, date_range, doy_range, 2)
        
    ### execute CCDC (GEE implementation)
    # (collection, breakpointBands, tmaskBands, minObservations, chiSquareProbability, minNumOfYearsScaler, dateFormat, lambda, maxIterations)
    ccdc_result = ee.Algorithms.TemporalSegmentation.Ccdc(gee_data, breakpoint_bands, tmask, numObs, chi, minYears, 2, lda)

    ### retrieve ccdc_result from server
    ccdc_result_info = ccdc_result.reduceRegion(ee.Reducer.toList(),
                                                point,
                                                scale=gee_scale).getInfo()
    
    ### get time series from selected band
    gee_data_point = np.array(ee.List(gee_data.getRegion(geometry=point, scale=gee_scale)).getInfo())
    #print(gee_data_point)
    timeseries = {}
    stacked_gee_data = np.stack(gee_data_point[1:],axis=1)
    for i in range(len(gee_data_point[0])):
        timeseries[gee_data_point[0][i]] = stacked_gee_data[i]
    #timeseries is a dictionary with the followings keys: id, longitude, latitude, time, Blue, Green, Red...
    
    #timeseries = np.array(gee_data_point)

    # # check if nan_mask is all zeros, not clean data available
    # if not any(nan_mask):
    #     from CCD_Plugin.CCD_Plugin import CCD_Plugin
    #     CCD_Plugin.widget.MsgBar.pushMessage("Error: Not enough clean data to compute CCD for this point",
    #                                          level=Qgis.Warning, duration=5)
    #     return

    global ccd_results
    ccd_results = {(coords, date_range, doy_range, dataset, tuple(breakpoint_bands)):(ccdc_result_info, timeseries)}

    return ccdc_result_info, timeseries
