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

with the collaboration of Paulo Arevalo Orduz <parevalo@bu.edu>
https://github.com/parevalo/gee-ccdc-tools

"""


def collection_filtering(point, collection_name, date_range, doy_range):
    import ee
    # Filter collection by point and date
    collection = ee.ImageCollection(collection_name)\
        .filterBounds(point)\
        .filterDate(ee.Date(date_range[0]), ee.Date(date_range[1]))\
        .filter(ee.Filter.dayOfYear(doy_range[0], doy_range[1]))
    return collection


def prepare_L4L5_C1(image):
    import ee
    band_list = ['B1', 'B2', 'B3', 'B4', 'B5', 'B7', 'B6', 'pixel_qa']
    name_list = ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2', 'Temp', 'pixel_qa']
    scaling = [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1]
    scaled = ee.Image(image).select(band_list).rename(name_list).divide(ee.Image.constant(scaling))

    validQA = [66, 130, 68, 132]
    mask1 = ee.Image(image).select(['pixel_qa']).remap(validQA, ee.List.repeat(1, len(validQA)), 0)
    # Gat valid data mask, for pixels without band saturation
    mask2 = image.select('radsat_qa').eq(0)
    mask3 = image.select(band_list[0:-1]).reduce(ee.Reducer.min()).gt(0)
    # Mask hazy pixels. Aggressively filters too many images in arid regions (e.g Egypt)
    # unless we force include 'nodata' values by unmasking
    mask4 = image.select("sr_atmos_opacity").unmask().lt(300)
    return ee.Image(image).addBands(scaled).updateMask(mask1.And(mask2).And(mask3).And(mask4)).select(name_list)


def prepare_L7_C1(image):
    import ee
    band_list = ['B1', 'B2', 'B3', 'B4', 'B5', 'B7', 'B6', 'pixel_qa']
    name_list = ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2', 'Temp', 'pixel_qa']
    scaling = [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1]
    scaled = ee.Image(image).select(band_list).rename(name_list).divide(ee.Image.constant(scaling))

    validQA = [66, 130, 68, 132]
    mask1 = ee.Image(image).select(['pixel_qa']).remap(validQA, ee.List.repeat(1, len(validQA)), 0)
    # Gat valid data mask, for pixels without band saturation
    mask2 = image.select('radsat_qa').eq(0)
    mask3 = image.select(band_list[0:-1]).reduce(ee.Reducer.min()).gt(0)
    # Mask hazy pixels. Aggressively filters too many images in arid regions (e.g Egypt)
    # unless we force include 'nodata' values by unmasking
    mask4 = image.select("sr_atmos_opacity").unmask().lt(300)
    # Slightly erode bands to get rid of artifacts due to scan lines
    mask5 = ee.Image(image).mask().reduce(ee.Reducer.min()).focal_min(2.5)
    return ee.Image(image).addBands(scaled).updateMask(mask1.And(mask2).And(mask3).And(mask4).And(mask5)).select(name_list)


def prepare_L8_C1(image):
    import ee
    band_list = ['B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B10', 'pixel_qa']
    name_list = ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2', 'Temp', 'pixel_qa']
    scaling = [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1]
    scaled = ee.Image(image).select(band_list).rename(name_list).divide(ee.Image.constant(scaling))

    validTOA = [66, 68, 72, 80, 96, 100, 130, 132, 136, 144, 160, 164]
    validQA = [322, 386, 324, 388, 836, 900]
    mask1 = ee.Image(image).select(['pixel_qa']).remap(validQA, ee.List.repeat(1, len(validQA)), 0)
    mask2 = image.select('radsat_qa').eq(0)
    mask3 = image.select(band_list[0:-1]).reduce(ee.Reducer.min()).gt(0)
    mask4 = ee.Image(image).select(['sr_aerosol']).remap(validTOA, ee.List.repeat(1, len(validTOA)), 0)
    return ee.Image(image).addBands(scaled).updateMask(mask1.And(mask2).And(mask3).And(mask4)).select(name_list)


def prepare_L4L5L7_C2(image):
    import ee
    band_list = ['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B7', 'ST_B6', 'QA_PIXEL']
    name_list = ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2', 'Temp', 'pixel_qa']
    subBand = ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2']
    scaling = [1]*8  #[10000, 10000, 10000, 10000, 10000, 10000, 10, 1]

    opticalBands = image.select('SR_B.').multiply(0.0000275).add(-0.2)
    thermalBand = image.select('ST_B6').multiply(0.00341802).add(149.0)
    no_scaled = opticalBands.addBands(thermalBand, None, True).addBands(image.select(['QA_PIXEL']), None, True)\
        .select(band_list).rename(name_list)
    scaled = no_scaled.multiply(ee.Image.constant(scaling))

    validQA = [5440, 5504]
    mask1 = ee.Image(image).select(['QA_PIXEL']).remap(validQA, ee.List.repeat(1, len(validQA)), 0)
    # Gat valid data mask, for pixels without band saturation
    mask2 = image.select('QA_RADSAT').eq(0)
    mask3 = no_scaled.select(subBand).reduce(ee.Reducer.min()).gt(0)
    mask4 = no_scaled.select(subBand).reduce(ee.Reducer.max()).lt(1)
    # Mask hazy pixels using AOD threshold
    mask5 = (image.select("SR_ATMOS_OPACITY").unmask(-1)).lt(300)
    return ee.Image(image).addBands(scaled).updateMask(mask1.And(mask2).And(mask3).And(mask4).And(mask5)).select(name_list)


def prepare_L8L9_C2(image):
    import ee
    band_list = ['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6', 'SR_B7', 'ST_B10', 'QA_PIXEL']
    name_list = ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2', 'Temp', 'pixel_qa']
    subBand = ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2']
    scaling = [1]*8  #[10000, 10000, 10000, 10000, 10000, 10000, 10, 1]

    opticalBands = image.select('SR_B.').multiply(0.0000275).add(-0.2)
    thermalBand = image.select('ST_B10').multiply(0.00341802).add(149.0)
    no_scaled = opticalBands.addBands(thermalBand, None, True).addBands(image.select(['QA_PIXEL']), None, True)\
        .select(band_list).rename(name_list)
    scaled = no_scaled.multiply(ee.Image.constant(scaling))

    validTOA = [2, 4, 32, 66, 68, 96, 100, 130, 132, 160, 164]
    validQA = [21824, 21888]  # 21826, 21890
    mask1 = ee.Image(image).select(['QA_PIXEL']).remap(validQA, ee.List.repeat(1, len(validQA)), 0)
    mask2 = image.select('QA_RADSAT').eq(0)
    # Assume that all saturated pixels equal to 20000
    mask3 = no_scaled.select(subBand).reduce(ee.Reducer.min()).gt(0)
    mask4 = no_scaled.select(subBand).reduce(ee.Reducer.max()).lt(1)
    mask5 = ee.Image(image).select(['SR_QA_AEROSOL']).remap(validTOA, ee.List.repeat(1, len(validTOA)), 0)
    return ee.Image(image).addBands(scaled).updateMask(mask1.And(mask2).And(mask3).And(mask4).And(mask5)).select(name_list)


# filter and merge collections
def get_gee_data_landsat(coords, date_range, doy_range, collection):
    import ee
    point = ee.Geometry.Point(coords)

    if collection == 1:
        l4 = collection_filtering(point, 'LANDSAT/LT04/C01/T1_SR', date_range, doy_range)
        l4_prepared = l4.map(prepare_L4L5_C1)

        l5 = collection_filtering(point, 'LANDSAT/LT05/C01/T1_SR', date_range, doy_range)
        l5_prepared = l5.map(prepare_L4L5_C1)

        l7 = collection_filtering(point, 'LANDSAT/LE07/C01/T1_SR', date_range, doy_range)
        l7_prepared = l7.map(prepare_L7_C1)

        l8 = collection_filtering(point, 'LANDSAT/LC08/C01/T1_SR', date_range, doy_range)
        l8_prepared = l8.map(prepare_L8_C1)

        all_scenes = ee.ImageCollection(l4_prepared).merge(l5_prepared).merge(l7_prepared)\
                                        .merge(l8_prepared).sort('system:time_start')

    if collection == 2:
        l4 = collection_filtering(point, 'LANDSAT/LT04/C02/T1_L2', date_range, doy_range)
        l4_prepared = l4.map(prepare_L4L5L7_C2)

        l5 = collection_filtering(point, 'LANDSAT/LT05/C02/T1_L2', date_range, doy_range)
        l5_prepared = l5.map(prepare_L4L5L7_C2)

        l7 = collection_filtering(point, 'LANDSAT/LE07/C02/T1_L2', date_range, doy_range)
        l7_prepared = l7.map(prepare_L4L5L7_C2)

        l8 = collection_filtering(point, 'LANDSAT/LC08/C02/T1_L2', date_range, doy_range)
        l8_prepared = l8.map(prepare_L8L9_C2)

        l9 = collection_filtering(point, 'LANDSAT/LC09/C02/T1_L2', date_range, doy_range)
        l9_prepared = l9.map(prepare_L8L9_C2)

        all_scenes = ee.ImageCollection(l4_prepared).merge(l5_prepared).merge(l7_prepared)\
                                        .merge(l8_prepared).merge(l9_prepared).sort('system:time_start')

    # Add indices: 'NBR', 'NDVI', 'EVI', 'EVI2', 'BRIGHTNESS', 'GREENNESS', 'WETNESS'
    all_gee_data = all_scenes.map(lambda image: image.addBands([
        image.normalizedDifference(['NIR', 'SWIR2']).rename('NBR'),
        image.normalizedDifference(['NIR', 'Red']).rename('NDVI'),
        image.expression('2.5 * ((NIR - Red) / (NIR + 6 * Red - 7.5 * Blue + 1))',
                         {'NIR': image.select('NIR'),
                          'Red': image.select('Red'),
                          'Blue': image.select('Blue')}).rename('EVI'),
        image.expression('2.5 * ((NIR - Red) / (NIR + 2.4 * Red + 1))',
                         {'NIR': image.select('NIR'),
                          'Red': image.select('Red')}).rename('EVI2'),
        # Brightness, Greenness, Wetness based on:
        # https://yceo.yale.edu/tasseled-cap-transform-landsat-8-oli
        # Muhammad Hasan Ali Baig, Lifu Zhang, Tong Shuai & Qingxi Tong (2014) Derivation of a tasselled cap
        # transformation based on Landsat 8 at-satellite reflectance, Remote Sensing Letters, 5:5, 423-431,
        # DOI: 10.1080/2150704X.2014.915434
        image.expression('0.3029 * Blue + 0.2786 * Green + 0.4733 * Red + 0.5599 * NIR + 0.508 * SWIR1 + 0.1872 * SWIR2',
                         {'Blue': image.select('Blue'),
                          'Green': image.select('Green'),
                          'Red': image.select('Red'),
                          'NIR': image.select('NIR'),
                          'SWIR1': image.select('SWIR1'),
                          'SWIR2': image.select('SWIR2')}).rename('BRIGHTNESS'),
        image.expression('-0.2941 * Blue - 0.243 * Green - 0.5424 * Red + 0.7276 * NIR + 0.0713 * SWIR1 - 0.1608 * SWIR2',
                         {'Blue': image.select('Blue'),
                          'Green': image.select('Green'),
                          'Red': image.select('Red'),
                          'NIR': image.select('NIR'),
                          'SWIR1': image.select('SWIR1'),
                          'SWIR2': image.select('SWIR2')}).rename('GREENNESS'),
        image.expression('0.1511 * Blue + 0.1973 * Green + 0.3283 * Red + 0.3407 * NIR - 0.7117 * SWIR1 - 0.4559 * SWIR2',
                         {'Blue': image.select('Blue'),
                          'Green': image.select('Green'),
                          'Red': image.select('Red'),
                          'NIR': image.select('NIR'),
                          'SWIR1': image.select('SWIR1'),
                          'SWIR2': image.select('SWIR2')}).rename('WETNESS')
    ]))

    
    filtered_col = all_gee_data.filter("WRS_ROW < 122").filterBounds(point)

    return filtered_col

# Run everything
# import ee
# ee.Initialize()
# coords = [-72.500634, 1.90668]
# year_range = (2000, 2010)
# doy_range = (1, 365)
#
# click_col = get_gee_data_landsat(coords, year_range, doy_range, 2)
#
# print(click_col)
