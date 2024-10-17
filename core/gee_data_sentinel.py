"""
/***************************************************************************
 CCDC at Point Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2023-2023 by Daniel Moraes
        email                : moraesd90@gmail.com
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


def get_gee_data_sentinel(coords, date_range, doy_range, name, cloud_filter='s2cloudless'):
    import ee
    geometry = ee.Geometry.Point(coords)
    if name == 'Sentinel-2':
        name = 'COPERNICUS/S2_SR_HARMONIZED'
    # get image collection
    img_col = ee.ImageCollection(name).filterBounds(geometry).filterDate(ee.Date(date_range[0]), ee.Date(date_range[1]))\
        .filter(ee.Filter.dayOfYear(doy_range[0], doy_range[1]))

    # prepare bands
    img_col = img_col.map(prepareBands)

    # add indices
    # add NDVI
    img_col = img_col.map(addNDVI)
    # add NBR
    img_col = img_col.map(addNBR)
    # add EVI
    img_col = img_col.map(addEVI)
    # add EVI2
    img_col = img_col.map(addEVI2)
    # add Brightness
    img_col = img_col.map(addBrightness)
    # add Greeness
    img_col = img_col.map(addGreeness)
    # add Wetness
    img_col = img_col.map(addWetness)

    # apply cloud filter
    if cloud_filter == 'Sen2Cor':
        img_col_filtered = img_col.map(filterS2_level2A)
    elif cloud_filter == 's2cloudless':
        # get cloud probability collection
        s2_cloudprob = ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY').filterBounds(geometry).filterDate(
            ee.Date(date_range[0]), ee.Date(date_range[1])).filter(ee.Filter.dayOfYear(doy_range[0], doy_range[1]))
        img_col_filtered = filterS2cloudless(img_col, s2_cloudprob)
    elif cloud_filter == 'No Mask':
        img_col_filtered = img_col

    # names_original = ['B2','B3','B4','B8','B11','B12','NDVI','NBR','EVI','EVI2','BRIGHTNESS','GREENNESS','WETNESS']
    names_renamed = ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2', 'NDVI', 'NBR', 'EVI', 'EVI2', 'BRIGHTNESS',
                     'GREENNESS', 'WETNESS']
    img_col_filtered_renamed = img_col_filtered.select(names_renamed)

    return img_col_filtered_renamed


def prepareBands(image):
    blue = image.select('B2').rename('Blue').divide(10000)
    green = image.select('B3').rename('Green').divide(10000)
    red = image.select('B4').rename('Red').divide(10000)
    nir = image.select('B8').rename('NIR').divide(10000)
    swir1 = image.select('B11').rename('SWIR1').divide(10000)
    swir2 = image.select('B12').rename('SWIR2').divide(10000)

    return image.addBands(blue).addBands(green).addBands(red).addBands(nir).addBands(swir1).addBands(swir2)
    


def addNDVI(image):
    ndvi = image.normalizedDifference(['NIR', 'Red'])
    return image.addBands(ndvi.rename('NDVI'))


def addNBR(image):
    nbr = image.normalizedDifference(['NIR', 'SWIR2'])
    return image.addBands(nbr.rename('NBR'))


def addEVI(image):
    evi = image.expression('2.5 * ((NIR-Red) / (NIR + 6 * Red - 7.5* Blue +1))',
        {'NIR': image.select('NIR'), 'Red': image.select('Red'), 'Blue': image.select('Blue')})
    return image.addBands(evi.rename('EVI'))


def addEVI2(image):
    evi2 = image.expression('2.5 * ((NIR - Red) / (NIR + 2.4 * Red + 1))',
                            {'NIR': image.select('NIR'), 'Red': image.select('Red')})
    return image.addBands(evi2.rename('EVI2'))


# Brightness, Greenness, Wetness based on:
# Shi, T., & Xu, H. (2019). Derivation of tasseled cap transformation coefficients for Sentinel-2 MSI at-sensor
# reflectance data. IEEE Journal of Selected Topics in Applied Earth Observations and Remote Sensing, 12(10), 4038-4048.
# https://doi.org/10.1109/JSTARS.2019.2938388

def addBrightness(image):
    brightness = image.expression('0.3510 * Blue + 0.3813 * Green + 0.3437 * Red + 0.7196 * NIR + 0.2396 * SWIR1 + 0.1949 * SWIR2',
                                  {'Blue': image.select('Blue'), 'Green': image.select('Green'), 'Red': image.select('Red'),
                                   'NIR': image.select('NIR'), 'SWIR1': image.select('SWIR1'), 'SWIR2': image.select('SWIR2')})
    return image.addBands(brightness.rename('BRIGHTNESS'))


def addGreeness(image):
    greeness = image.expression('- 0.3599 * Blue - 0.3533 * Green - 0.4734 * Red + 0.6633 * NIR + 0.0087 * SWIR1 - 0.2856 * SWIR2',
                                {'Blue': image.select('Blue'), 'Green': image.select('Green'), 'Red': image.select('Red'),
                                 'NIR': image.select('NIR'), 'SWIR1': image.select('SWIR1'), 'SWIR2': image.select('SWIR2')})
    return image.addBands(greeness.rename('GREENNESS'))


def addWetness(image):
    wetness = image.expression('0.2578 * Blue + 0.2305 * Green + 0.0883 * Red + 0.1071 * NIR - 0.7611 * SWIR1 - 0.5308 * SWIR2',
                               {'Blue': image.select('Blue'), 'Green': image.select('Green'), 'Red': image.select('Red'),
                                'NIR': image.select('NIR'), 'SWIR1': image.select('SWIR1'), 'SWIR2': image.select('SWIR2')})
    return image.addBands(wetness.rename('WETNESS'))


# SCL cloud/shadow filter
def filterS2_level2A(image):
    import ee
    SCL = image.select('SCL')
    mask01 = ee.Image(0).where((SCL.lt(8)).And(SCL.gt(3)), 1)  # Put a 1 on good pixels
    # (SCL.gt(3),1)
    return image.updateMask(mask01)


# s2cloudless filter
def filterS2cloudless(S2SRCol, S2CloudCol):
    import ee
    CLOUD_FILTER = 60
    CLD_PRB_THRESH = 50
    NIR_DRK_THRESH = 0.2
    CLD_PRJ_DIST = 1
    BUFFER = 50

    # filter images based on cloudy percentage (metadata)
    S2SRCol = S2SRCol.filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', CLOUD_FILTER))

    # join S2SR with S2CloudCol
    joined = ee.ImageCollection(ee.Join.saveFirst('s2cloudless').apply(primary=S2SRCol, secondary=S2CloudCol,
        condition=ee.Filter.equals(leftField='system:index', rightField='system:index')))

    def add_cloud_bands(img):
        # Get s2cloudless image, subset the probability band.
        cld_prb = ee.Image(img.get('s2cloudless')).select('probability')
        # Condition s2cloudless by the probability threshold value
        is_cloud = cld_prb.gt(CLD_PRB_THRESH).rename('clouds')
        # Add the cloud probability layer and cloud mask as image bands
        return img.addBands(ee.Image([cld_prb, is_cloud]))

    def add_shadow_bands(img):
        # Identify water pixels from the SCL band
        not_water = img.select('SCL').neq(6)
        # Identify dark NIR pixels that are not water (potential cloud shadow pixels)
        SR_BAND_SCALE = 1e4
        dark_pixels = img.select('B8').lt(NIR_DRK_THRESH * SR_BAND_SCALE).multiply(not_water).rename('dark_pixels')
        # Determine the direction to project cloud shadow from clouds (assumes UTM projection)
        shadow_azimuth = ee.Number(90).subtract(ee.Number(img.get('MEAN_SOLAR_AZIMUTH_ANGLE')))
        # Project shadows from clouds for the distance specified by the CLD_PRJ_DIST input
        cld_proj = (img.select('clouds').directionalDistanceTransform(shadow_azimuth, CLD_PRJ_DIST * 10).reproject(
            crs=img.select(0).projection(), scale=100).select('distance').mask().rename('cloud_transform'))
        # Identify the intersection of dark pixels with cloud shadow projection
        shadows = cld_proj.multiply(dark_pixels).rename('shadows')
        # Add dark pixels, cloud projection, and identified shadows as image bands
        return img.addBands(ee.Image([dark_pixels, cld_proj, shadows]))

    def add_cld_shdw_mask(img):
        # Add cloud component bands.
        img_cloud = add_cloud_bands(img)
        # Add cloud shadow component bands.
        img_cloud_shadow = add_shadow_bands(img_cloud)
        # Combine cloud and shadow mask, set cloud and shadow as value 1, else 0.
        is_cld_shdw = img_cloud_shadow.select('clouds').add(img_cloud_shadow.select('shadows')).gt(0)
        # Remove small cloud-shadow patches and dilate remaining pixels by BUFFER input
        # 20 m scale is for speed, and assumes clouds don't require 10 m precision
        is_cld_shdw = (is_cld_shdw.focalMin(2).focalMax(BUFFER * 2 / 20).reproject(crs=img.select([0]).projection(),
                                                                                   scale=20).rename('cloudmask'))
        # Add the final cloud-shadow mask to the image
        return img_cloud_shadow.addBands(is_cld_shdw)

    def apply_cld_shdw_mask(img):
        # Subset the cloudmask band and invert it so clouds/shadow are 0, else 1.
        not_cld_shdw = img.select('cloudmask').Not()
        # Subset reflectance bands and update their masks, return the result.
        return img.updateMask(not_cld_shdw)

    s2_sr = joined.map(add_cld_shdw_mask).map(apply_cld_shdw_mask)

    return s2_sr
