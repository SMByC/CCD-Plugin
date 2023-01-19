# CCD-Plugin

CCD-Plugin implements the Continuous Change Detection algorithm using PyCCD and Google Earth Engine to analyze the trend and breakpoints of change over multi-year of the time series for a specific Landsat band at a given coordinate.

![](screenshot.png)

The plugin utilizes Google Earth Engine to obtain data for the specified coordinate, filtering for high-quality data only, for all Landsat available right now 4, 5, 7, 8 and 9. The plugin then uses [PyCCD](https://code.usgs.gov/lcmap/pyccd) to calculate the trend and breakpoints using the Continuous Change Detection algorithm.

- Zhu, Z., & Woodcock, C. E. (2014). Continuous change detection and classification of land cover using all available Landsat data. Remote sensing of Environment, 144, 152-171. https://doi.org/10.1016/j.rse.2014.01.011

- Arévalo, P., Bullock, E.L., Woodcock, C.E., Olofsson, P., (2020). A Suite of Tools for Continuous Land Change Monitoring in Google Earth Engine. Front. Clim. 2. https://doi.org/10.3389/fclim.2020.576740

## Installation

The plugin needs to work:

- Google Earth Engine [plugin](https://gee-community.github.io/qgis-earthengine-plugin/ ): The user needs to have this plugin installed and an active Google Earth Engine (EE) account.
- PyCCD, numpy, scipy and scikit-learn packages are already included in the plugin.

## About us

CCD-Plugin was developing, designed and implemented by the Group of Forest and Carbon Monitoring System (SMByC), operated by the Institute of Hydrology, Meteorology and Environmental Studies (IDEAM) - Colombia.

Author and developer: *Xavier C. Llano* *<xavier.corredor.llano@gmail.com>*  
Theoretical support, tester and product verification: SMByC-PDI group

## License

CCD-Plugin is a free/libre software and is licensed under the GNU General Public License.
