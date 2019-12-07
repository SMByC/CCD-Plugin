# -*- coding: utf-8 -*-
"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019 by Xavier Corredor Llano, SMByC
        email                : xcorredorl@ideam.gov.co
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

"""

import ee


# Filter collection by point and date
def collection_filtering(point, collection_name, year_range, doy_range):
    collection = ee.ImageCollection(collection_name)\
    .filterBounds(point)\
    .filter(ee.Filter.calendarRange(year_range[0], year_range[1], 'year'))\
    .filter(ee.Filter.dayOfYear(doy_range[0],doy_range[1]))
    return collection


# Cloud masking for C1, L4-L7. Operators capitalized to
# avoid confusing with internal Python operators
def cloud_mask_l4_7_C1(img):
    pqa = ee.Image(img).select(['pixel_qa'])
    mask = (pqa.eq(66)).Or(pqa.eq(130))\
    .Or(pqa.eq(68)).Or(pqa.eq(132))
    return ee.Image(img).updateMask(mask)


# Cloud masking for C1, L8
def cloud_mask_l8_C1(img):
    pqa = ee.Image(img).select(['pixel_qa'])
    mask = (pqa.eq(322)).Or(pqa.eq(386)).Or(pqa.eq(324))\
    .Or(pqa.eq(388)).Or(pqa.eq(836)).Or(pqa.eq(900))
    return ee.Image(img).updateMask(mask)


def stack_renamer_l4_7_C1(img):
    band_list = ['B1', 'B2', 'B3', 'B4', 'B5', 'B7',  'B6', 'pixel_qa']
    name_list = ['BLUE', 'GREEN', 'RED', 'NIR', 'SWIR1', 'SWIR2', 'THERMAL',
                 'pixel_qa']
    return ee.Image(img).select(band_list).rename(name_list)


def stack_renamer_l8_C1(img):
    band_list = ['B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B10', 'pixel_qa']
    name_list = ['BLUE', 'GREEN', 'RED', 'NIR', 'SWIR1', 'SWIR2', 'THERMAL',
                 'pixel_qa']
    return ee.Image(img).select(band_list).rename(name_list)


# filter and merge collections
def get_full_collection(coords, year_range, doy_range):
    point = ee.Geometry.Point(coords)
    l8_renamed = collection_filtering(point, 'LANDSAT/LC08/C01/T1_SR', year_range, doy_range)\
        .map(stack_renamer_l8_C1)
    l8_filtered1 = l8_renamed.map(cloud_mask_l8_C1)

    l7_renamed = collection_filtering(point, 'LANDSAT/LE07/C01/T1_SR', year_range, doy_range)\
        .map(stack_renamer_l4_7_C1);
    l7_filtered1 = l7_renamed.map(cloud_mask_l4_7_C1)

    l5_renamed = collection_filtering(point, 'LANDSAT/LT05/C01/T1_SR', year_range, doy_range)\
        .map(stack_renamer_l4_7_C1)
    l5_filtered1 = l5_renamed.map(cloud_mask_l4_7_C1)

    all_scenes = ee.ImageCollection((l8_filtered1.merge(l7_filtered1))\
                .merge(l5_filtered1)).sort('system:time_start')
    
    # Return merged image collection
    return all_scenes


# Get time series for location
def get_data_full(collection, coords):
    point = ee.Geometry.Point(coords)
    # Sample for a time series of values at the point.
    filtered_col = collection.filterBounds(point)
    geom_values = filtered_col.getRegion(geometry=point, scale=30)
    # I DON'T REMEMBER WHAT THIS RETURNS, PROBABLY A JSON
    data = ee.List(geom_values).getInfo()
    
    return data

## Run everything
#coords = [-72.500634, 1.90668]
#year_range = (2000, 2020)
#doy_range = (1, 365)

#click_col = get_full_collection(coords, year_range, doy_range)
#click_df = get_data_full(click_col, coords)

#print(click_df)
