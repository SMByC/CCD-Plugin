# CCD-Plugin

The CCD-Plugin uses Google Earth Engine to get Landsat or Sentinel-2 datasets and run the Continuous Change Detection
(CCDC) algorithm to analyze the trends and breakpoints of change over multi-year time series at a given coordinate.

![](screenshot.webp)

Pick a point on the map and the plugin pulls every clear observation for that pixel out of the Earth Engine
catalog, hands the series to CCDC, and plots what comes back: the observations themselves, the model fitted to
each stable period, and the dates where the series broke.

It answers one question well: *what happened at this pixel, and when?* It is a point tool for looking at a time
series, not a mapping tool; it does not classify or produce a change map.

## How CCDC works

CCDC ([Zhu & Woodcock, 2014](https://doi.org/10.1016/j.rse.2014.01.011)) treats a pixel's spectral history as a
signal to be modelled rather than a stack of images to be compared. Instead of differencing two dates, or compositing
by year and losing the seasons, it uses *every* clear observation as it arrives, which is what "continuous"
means here, and asks whether each new one still agrees with the behaviour established so far.

The full algorithm has two halves: temporal segmentation (finding the breaks) and classification (labelling what each
segment is). The plugin runs the first half, through Earth Engine's
[`ee.Algorithms.TemporalSegmentation.Ccdc`](https://developers.google.com/earth-engine/apidocs/ee-algorithms-temporalsegmentation-ccdc).

### The model

For each band it is given, CCDC fits by LASSO regression a harmonic model of time:

```
ρ(t) = a₀ + a₁·t + Σ (k = 1..3)  [ b_k·cos(2πkt/T) + c_k·sin(2πkt/T) ],   T = 1 year
```

That is eight coefficients per band, per segment, and they are worth reading as a description of the pixel:

| Term | Coefficients | What it captures |
|---|---|---|
| Intercept | `a₀` | the overall reflectance level |
| Slope | `a₁` | the long-term trend: gradual degradation, regrowth, drying |
| 1st harmonic | `b₁, c₁` | the annual cycle: phenology, the wet/dry season |
| 2nd, 3rd harmonics | `b₂, c₂, b₃, c₃` | the *shape* of that cycle: a double crop, a short sharp green-up |

Each fit also carries an **RMSE**, the typical scatter of the observations around the curve. That number is what makes
the algorithm self-calibrating: a noisy pixel is allowed noisy observations before anything counts as a change.

### How a break is found

Once a segment has a model, every new observation is predicted from it. The residual is divided by that band's RMSE,
so it is measured in units of *this pixel's* normal variability, and the normalised residuals are combined across the
breakpoint bands into a single statistic tested against a **chi-square** threshold.

One anomalous observation proves nothing; it is far more likely an undetected cloud or shadow. A break is flagged
only when **`Num Obs` consecutive** observations all fall outside the threshold. The break is dated at the first of
them, that segment is closed, and once enough new observations accumulate a fresh model is fitted for what follows.

This is also why the plugin can afford permissive per-image cloud masking: requiring consecutive anomalies makes
isolated bad pixels harmless, and CCDC's own **TMask** screen catches the rest.

### Segments

A segment is one stable period: a start date, an end date, its eight coefficients per band, and its RMSE. The plot
draws one curve per segment, each in its own colour, so consecutive fits read apart instead of looking like a single
broken line. A break between two segments is a real change in how the pixel behaves. That is not necessarily
deforestation, just a discontinuity the previous model could no longer explain.

## Data

### Datasets

- **Landsat C2** (30 m): Collection 2 surface reflectance, Tier 1, from Landsat 4, 5, 7, 8 and 9.
  1982 → present, with dense coverage from 1984.
- **Sentinel-2** (10 m): the Harmonized L2A surface reflectance collection.
  2017-03 → present, global only from late 2018.

> Landsat Collection 1 was decommissioned by USGS and removed from the GEE data catalog, so it is no longer supported.

Both are exposed to CCDC through the same band schema, so the algorithm, the cache and the plot are dataset-agnostic.
The observations and the fitted model are sampled on the source image grid, not on a resampled one, so both come from
exactly the pixel you clicked.

### Bands and indices

- **Bands:** Blue, Green, Red, NIR, SWIR1, SWIR2
- **Indices:** NDVI, NBR, EVI, EVI2, BRIGHTNESS, GREENNESS, WETNESS

The tasseled cap indices (BRIGHTNESS/GREENNESS/WETNESS) use per-sensor coefficients so that the Landsat sensors land
in one common domain, which is what keeps the L5/L7/L8 transitions from registering as breaks.

### Cloud masking

Landsat always uses the Collection 2 QA bands: CFmask (`QA_PIXEL`) for fill, cloud, dilated cloud, cloud shadow,
snow and (on Landsat 8/9 only) cirrus; `QA_RADSAT` for saturation; and the per-sensor haze product
(`SR_ATMOS_OPACITY` for TM/ETM+, `SR_QA_AEROSOL` for OLI/OLI-2). The cloud *confidence* bits are deliberately
not used, so low and medium confidence clear pixels are kept, and reflectance outside the valid range is dropped.
The Landsat 7 SLC-off scan gaps are fill, so the QA fill bit already removes them.

For Sentinel-2 the mask is selectable in *Advanced settings*:

| Filter | What it uses | When to prefer it |
|---|---|---|
| **Cloud Score+** (default) | `cs_cdf` from `GOOGLE/CLOUD_SCORE_PLUS`, scoring cloud, cirrus, haze and shadow together | Best quality/retention balance; recommended |
| **s2cloudless** | `COPERNICUS/S2_CLOUD_PROBABILITY` plus a solar-geometry shadow projection | Comparison, or to reproduce older results |
| **Sen2Cor** | the L2A scene classification (`SCL`), dilated | When the SCL classes are known to work well for the area |
| **No Mask** | nothing; CCDC's TMask alone screens the series | Very cloudy areas where any mask leaves too few observations |

In every case the masks are per-pixel only: a scene is never dropped because the rest of the tile is cloudy,
since only the pixel at the requested coordinate matters.

## References

- Zhu, Z., & Woodcock, C. E. (2014). Continuous change detection and classification of land cover using all available Landsat data. Remote sensing of Environment, 144, 152-171. https://doi.org/10.1016/j.rse.2014.01.011

- Arévalo, P., Bullock, E.L., Woodcock, C.E., Olofsson, P., (2020). A Suite of Tools for Continuous Land Change Monitoring in Google Earth Engine. Front. Clim. 2. https://doi.org/10.3389/fclim.2020.576740

- Crist, E.P., & Cicone, R.C. (1984). A physically-based transformation of Thematic Mapper data — the TM Tasseled Cap. IEEE Transactions on Geoscience and Remote Sensing, 22(3), 256-263. *(BRIGHTNESS/GREENNESS/WETNESS for Landsat 4, 5 and 7)*

- Baig, M.H.A., Zhang, L., Shuai, T., & Tong, Q. (2014). Derivation of a tasselled cap transformation based on Landsat 8 at-satellite reflectance. Remote Sensing Letters, 5(5), 423-431. https://doi.org/10.1080/2150704X.2014.915434 *(BRIGHTNESS/GREENNESS/WETNESS for Landsat 8 and 9)*

- Shi, T., & Xu, H. (2019). Derivation of tasseled cap transformation coefficients for Sentinel-2 MSI at-sensor reflectance data. IEEE JSTARS, 12(10), 4038-4048. https://doi.org/10.1109/JSTARS.2019.2938388 *(BRIGHTNESS/GREENNESS/WETNESS for Sentinel-2)*

- Pasquarella, V.J., Brown, C.F., Czerwinski, W., & Rucklidge, W.J. (2023). Comprehensive Quality Assessment of Optical Satellite Imagery Using Weakly Supervised Video Learning. CVPR Workshops, 2124-2134. *(Cloud Score+)*

## Installation

The plugin needs to work:

- **QGIS >= 4.0** with Qt 6 and Qt WebEngine.
- Google Earth Engine [plugin](https://gee-community.github.io/qgis-earthengine-plugin/ ): The user needs to have this plugin installed and an active Google Earth Engine (EE) account.
- Plotly. Most of the Qgis versions have this library inside, otherwise the plugin install it automatically.

Each numeric GitHub release includes `extlibs.zip`, built with the latest compatible dependencies resolved from
`requirements.txt`. If automatic installation is not available, download that asset from the release matching the
plugin version and extract its contents into an `extlibs` directory inside `CCD_Plugin`.

### Known issues

> **Download plot as a png:**
> It is not working inside the plugin, but it works when the plot is opened in a web browser

## About us

CCD-Plugin was developing, designed and implemented by the Group of Forest and Carbon Monitoring System (SMByC), operated by the Institute of Hydrology, Meteorology and Environmental Studies (IDEAM) - Colombia.

Author and developer: *Xavier C. Llano* *<xavier.corredor.llano@gmail.com>*  
Collaborator and co-developer: *Daniel Moraes* *<moraesd90@gmail.com>*  
Theoretical support, tester and product verification: *SMByC-PDI group*  

## License

CCD-Plugin is a free/libre software and is licensed under the GNU General Public License.
