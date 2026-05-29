from datetime import datetime

import ccd
import ee
import httplib2
import matplotlib.pyplot as plt
import numpy as np

ee.Initialize(http_transport=httplib2.Http())


# Filter collection by point and date
def collection_filtering(point, collection_name, year_range, doy_range):
    collection = (
        ee.ImageCollection(collection_name)
        .filterBounds(point)
        .filter(ee.Filter.calendarRange(year_range[0], year_range[1], "year"))
        .filter(ee.Filter.dayOfYear(doy_range[0], doy_range[1]))
    )
    return collection


def prepare_L4L5L7_C2(image):
    band_list = ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7", "ST_B6", "QA_PIXEL"]
    name_list = ["Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2", "Temp", "pixel_qa"]
    subBand = ["Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2"]

    opticalBands = image.select("SR_B.").multiply(0.0000275).add(-0.2)
    thermalBand = image.select("ST_B6").multiply(0.00341802).add(149.0)
    scaled = (
        opticalBands.addBands(thermalBand, None, True)
        .addBands(image.select(["QA_PIXEL"]), None, True)
        .select(band_list)
        .rename(name_list)
    )

    validQA = [5440, 5504]
    mask1 = ee.Image(image).select(["QA_PIXEL"]).remap(validQA, ee.List.repeat(1, len(validQA)), 0)
    # Gat valid data mask, for pixels without band saturation
    mask2 = image.select("QA_RADSAT").eq(0)
    mask3 = scaled.select(subBand).reduce(ee.Reducer.min()).gt(0)
    mask4 = scaled.select(subBand).reduce(ee.Reducer.max()).lt(1)
    # Mask hazy pixels using AOD threshold
    mask5 = (image.select("SR_ATMOS_OPACITY").unmask(-1)).lt(300)
    return (
        ee.Image(image).addBands(scaled).updateMask(mask1.And(mask2).And(mask3).And(mask4).And(mask5)).select(name_list)
    )


def prepare_L8L9_C2(image):
    band_list = ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7", "ST_B10", "QA_PIXEL"]
    name_list = ["Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2", "Temp", "pixel_qa"]
    subBand = ["Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2"]

    opticalBands = image.select("SR_B.").multiply(0.0000275).add(-0.2)
    thermalBand = image.select("ST_B10").multiply(0.00341802).add(149.0)
    scaled = (
        opticalBands.addBands(thermalBand, None, True)
        .addBands(image.select(["QA_PIXEL"]), None, True)
        .select(band_list)
        .rename(name_list)
    )

    validTOA = [2, 4, 32, 66, 68, 96, 100, 130, 132, 160, 164]
    validQA = [21824, 21888]  # 21826, 21890
    mask1 = ee.Image(image).select(["QA_PIXEL"]).remap(validQA, ee.List.repeat(1, len(validQA)), 0)
    mask2 = image.select("QA_RADSAT").eq(0)
    # Assume that all saturated pixels equal to 20000
    mask3 = scaled.select(subBand).reduce(ee.Reducer.min()).gt(0)
    mask4 = scaled.select(subBand).reduce(ee.Reducer.max()).lt(1)
    mask5 = ee.Image(image).select(["SR_QA_AEROSOL"]).remap(validTOA, ee.List.repeat(1, len(validTOA)), 0)
    return (
        ee.Image(image).addBands(scaled).updateMask(mask1.And(mask2).And(mask3).And(mask4).And(mask5)).select(name_list)
    )


# filter and merge collections
def get_full_collection(coords, year_range, doy_range, collection):
    point = ee.Geometry.Point(coords)

    if collection == 2:
        l4 = collection_filtering(point, "LANDSAT/LT04/C02/T1_L2", year_range, doy_range)
        l4_prepared = l4.map(prepare_L4L5L7_C2)

        l5 = collection_filtering(point, "LANDSAT/LT05/C02/T1_L2", year_range, doy_range)
        l5_prepared = l5.map(prepare_L4L5L7_C2)

        l7 = collection_filtering(point, "LANDSAT/LE07/C02/T1_L2", year_range, doy_range)
        l7_prepared = l7.map(prepare_L4L5L7_C2)

        l8 = collection_filtering(point, "LANDSAT/LC08/C02/T1_L2", year_range, doy_range)
        l8_prepared = l8.map(prepare_L8L9_C2)

        l9 = collection_filtering(point, "LANDSAT/LC09/C02/T1_L2", year_range, doy_range)
        l9_prepared = l9.map(prepare_L8L9_C2)

        all_scenes = ee.ImageCollection(
            l4_prepared.merge(l5_prepared).merge(l7_prepared).merge(l8_prepared).merge(l9_prepared)
        ).sort("system:time_start")

    # Return merged image collection
    return all_scenes


# Get time series for location
def get_data_full(collection, coords):
    point = ee.Geometry.Point(coords)
    # Sample for a time series of values at the point.
    filtered_col = collection.filter("WRS_ROW < 122").filterBounds(point)
    geom_values = filtered_col.getRegion(geometry=point, scale=30)
    data = ee.List(geom_values).getInfo()
    return data


# Run
coords = [-72.500634, 1.90668]
year_range = (2000, 2020)
doy_range = (1, 365)
collection = 2

data_collection = get_full_collection(coords, year_range, doy_range, collection)
data_point = get_data_full(data_collection, coords)[1::]

# generate a merge/fusion mask layer of nan/none values to filter all data
nan_masks = [[0 if dp[i] is None else 1 for dp in data_point] for i in range(3, 12)]
# fusion masks
nan_mask = [0 if 0 in m else 1 for m in zip(*nan_masks, strict=True)]


def mask(input_list, boolean_mask):
    return [i for i, b in zip(input_list, boolean_mask, strict=True) if b]


# get each features applying the mask
dates, blues, greens, reds, nirs, swir1s, swir2s, thermals, qas = (
    mask([dp[3] for dp in data_point], nan_mask),
    mask([dp[4] for dp in data_point], nan_mask),
    mask([dp[5] for dp in data_point], nan_mask),
    mask([dp[6] for dp in data_point], nan_mask),
    mask([dp[7] for dp in data_point], nan_mask),
    mask([dp[8] for dp in data_point], nan_mask),
    mask([dp[9] for dp in data_point], nan_mask),
    mask([dp[10] for dp in data_point], nan_mask),
    mask([dp[11] for dp in data_point], nan_mask),
)

# convert the dates from miliseconds unix time to ordinal
dates = np.array([datetime.fromtimestamp(int(str(int(d))[:-3])).toordinal() for d in dates])

results = ccd.detect(dates, blues, greens, reds, nirs, swir1s, swir2s, thermals, qas)

# plot

mask = np.array(results["processing_mask"], dtype=bool)
print("MASK", mask)
print(f"Start Date: {datetime.fromordinal(dates[0])}\nEnd Date: {datetime.fromordinal(dates[-1])}\n")

predicted_values = []
prediction_dates = []
break_dates = []
start_dates = []

for num, result in enumerate(results["change_models"]):
    print(f"Result: {num}")
    print("Start Date: {}".format(datetime.fromordinal(result["start_day"])))
    print("End Date: {}".format(datetime.fromordinal(result["end_day"])))
    print("Break Date: {}".format(datetime.fromordinal(result["break_day"])))
    print("QA: {}".format(result["curve_qa"]))
    print(
        "Norm: {}\n".format(
            np.linalg.norm(
                [
                    result["green"]["magnitude"],
                    result["red"]["magnitude"],
                    result["nir"]["magnitude"],
                    result["swir1"]["magnitude"],
                    result["swir2"]["magnitude"],
                ]
            )
        )
    )
    print("Change prob: {}".format(result["change_probability"]))

    days = np.arange(result["start_day"], result["end_day"] + 1)
    prediction_dates.append(days)
    break_dates.append(result["break_day"])
    start_dates.append(result["start_day"])

    intercept = result["green"]["intercept"]
    coef = result["green"]["coefficients"]

    predicted_values.append(
        intercept
        + coef[0] * days
        + coef[1] * np.cos(days * 1 * 2 * np.pi / 365.25)
        + coef[2] * np.sin(days * 1 * 2 * np.pi / 365.25)
        + coef[3] * np.cos(days * 2 * 2 * np.pi / 365.25)
        + coef[4] * np.sin(days * 2 * 2 * np.pi / 365.25)
        + coef[5] * np.cos(days * 3 * 2 * np.pi / 365.25)
        + coef[6] * np.sin(days * 3 * 2 * np.pi / 365.25)
    )

plt.style.use("ggplot")

fg = plt.figure(figsize=(16, 9), dpi=300)
a1 = fg.add_subplot(2, 1, 1, xlim=(min(dates), max(dates)))

# Predicted curves
for _preddate, _predvalue in zip(prediction_dates, predicted_values, strict=True):
    a1.plot(_preddate, _predvalue, "orange", linewidth=1)

band_data = np.array(greens)
a1.plot(dates[mask], band_data[mask], "g+")  # Observed values
a1.plot(dates[~mask], band_data[~mask], "k+")  # Observed values masked out
for b in break_dates:
    a1.axvline(b)
for s in start_dates:
    a1.axvline(s, color="r")
plt.show()
