# -*- coding: utf-8 -*-
"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019-2023 by Xavier Corredor Llano, SMByC
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

with the collaboration of:
    Paulo Arevalo Orduz <parevalo@bu.edu>
    Daniel Moraes <moraesd90@gmail.com>

"""
import tempfile
import numpy as np
from datetime import datetime, timedelta
import plotly
import plotly.graph_objects as go
import plotly.io as pio

###create artificial dates for plotting the regression (plug values into regression equation)
def createArtificialDates(date_range,first_date):
    import ee
 
    date_end = date_range[1]
    # create sequence of dates from first date to date_end, spaced by 5 days
    interval = 5 #days
    
    date_end_millis = ee.Date(date_end).millis().getInfo()
    
    num_intervals = int((date_end_millis-first_date)/(interval*24*60*60*1000))
    
    artificial_dates = [first_date+x*interval*24*60*60*1000 for x in range(num_intervals)]

    # adjust end of series
    if artificial_dates[-1]<date_end_millis:
        artificial_dates.append(date_end_millis)
    elif artificial_dates[-1]>date_end_millis:
        artificial_dates.pop(-1)
    artificial_dates = np.array(artificial_dates)

    return artificial_dates #in milli


def generate_plot(id, ccdc_result_info, timeseries, date_range, dataset, band_to_plot):
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    first_date = int(timeseries['time'][0]) #int(timeseries[1][3])
    # get artificial dates (required for plotting ccdc fitted curves)
    artificial_dates = createArtificialDates(date_range,first_date)

    # get number of fitted segments
    nsegments = len(ccdc_result_info['tBreak'][0])

    # cycle through each segment and plot the predicted values by pluggin into harmonic regression equation
    predicted_values = []
    prediction_dates = []
    for seg in range(nsegments):
        artificial_dates_seg = artificial_dates[(artificial_dates<=ccdc_result_info['tEnd'][0][seg])&(artificial_dates>=ccdc_result_info['tStart'][0][seg])]
        #include tEnd and tStart in the series, if not already included
        artificial_dates_seg = np.append(artificial_dates_seg, [ccdc_result_info['tEnd'][0][seg],ccdc_result_info['tStart'][0][seg]])
        artificial_dates_seg = np.sort(np.unique(artificial_dates_seg))

        coefs = ccdc_result_info['{}_coefs'.format(band_to_plot)][0][seg]
        pred = [coefs[0]+coefs[1]*t+
                coefs[2]*np.cos(t*1*2*np.pi/(365.25*24*60*60*1000))+
                coefs[3]*np.cos(t*1*2*np.pi/(365.25*24*60*60*1000))+
                coefs[4]*np.cos(t*2*2*np.pi/(365.25*24*60*60*1000))+
                coefs[5]*np.cos(t*2*2*np.pi/(365.25*24*60*60*1000))+
                coefs[6]*np.cos(t*3*2*np.pi/(365.25*24*60*60*1000))+
                coefs[7]*np.cos(t*3*2*np.pi/(365.25*24*60*60*1000))
                for t in artificial_dates_seg]

        predicted_values.append(pred)
        prediction_dates.append(artificial_dates_seg)

    # get start and break dates
    break_dates = ccdc_result_info['tBreak'][0].copy()
    if 0 in break_dates:
        break_dates.remove(0) #delete zero from break dates
    #start_dates = ccdc_result_info['tStart'][0]

    # get observed values (actual time series)
    dates_obs = timeseries['time'] #np.stack(timeseries,axis=1)[:][-2][1:].astype('int64')
    values_obs = np.array(timeseries[band_to_plot],dtype='float') #np.stack(timeseries,axis=1)[:][-1][1:].astype('float')
    datetime_min = datetime.fromtimestamp(np.min(dates_obs) / 1000)
    datetime_max = datetime.fromtimestamp(np.max(dates_obs) / 1000)

    ######## plot with plotly ########

    pio.templates.default = "plotly_white"
    fig = go.Figure()

    # plot observed values
    fig.add_trace(go.Scatter(x=[datetime.fromtimestamp(date / 1000) for date in dates_obs],
                             y=values_obs, name='observed<br>values', mode='markers',
                             marker=dict(color='#4498d4', size=6, opacity=1)))  # , symbol="cross"

    # Predicted curves
    curve_colors = ["#56ad74", "#a291e1", "#c69255", "#e274cf", "#5ea5c5"]*2
    for idx, (_preddate, _predvalue) in enumerate(zip(prediction_dates, predicted_values)):
        fig.add_trace(go.Scatter(x=[datetime.fromtimestamp(date / 1000) for date in _preddate],
                                 y=_predvalue, name='predicted<br>values ({})'.format(idx + 1), opacity=0.7,
                                 hovertemplate="%{y}", line=dict(width=2.4, color=curve_colors[idx])))

    # break lines
    #break_dates = list(set(start_dates+break_dates))  # delete duplicates
    for break_date in break_dates:
        fig.add_vline(x=break_date, line_width=1, line_dash="dash", line_color="red",
                      annotation_text=datetime.fromtimestamp(break_date / 1000).strftime("%Y-%m-%d"),
                      annotation_position="bottom right", annotation_textangle=90, opacity=0.6,
                      annotation_font_size=9, annotation_font_color="red")

    # add a fake line to add the legend for the break lines
    fig.add_trace(go.Scatter(x=[datetime_min]*2, y=[np.nanmin(values_obs)]*2, hoverinfo='skip',
                             mode='lines', line=dict(color='red', width=1, dash='dash'), name='break lines'))

    # get longitude and latitude from CCD_PluginDockWidget
    lon = CCD_Plugin.inst[id].widget.longitude.value()
    lat = CCD_Plugin.inst[id].widget.latitude.value()

    fig.update_layout(
        title={
            'text': "Lat: {} Lon: {}".format(lat, lon),
            'y': 0.98,
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top'},
        margin=go.layout.Margin(
            l=0,
            r=0,
            b=0,
            t=22,
            pad=0
        ),
        paper_bgcolor="white",
    )

    fig.update_traces(hovertemplate='%{y:.4f}<br>%{x|%d-%b-%Y}')
    fig.update_xaxes(title_text=None, fixedrange=False, ticklabelmode="period", dtick="M12",
                     tick0=datetime(datetime_min.year, 1, 1),tickformat="%Y", automargin=True)
    # update min and max xaxes margins
    margin_days = int((datetime_max - datetime_min).days*0.01)
    fig.update_xaxes(range=[datetime_min - timedelta(days=margin_days), datetime_max + timedelta(days=margin_days)])
    
    if band_to_plot in ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2']:
        title = "Surface Reflectance - {} ({})".format(band_to_plot, dataset)
    if band_to_plot in ["NBR", "NDVI", "EVI", "EVI2", "BRIGHTNESS", "GREENNESS", "WETNESS"]:
        title = "Index - {} ({})".format(band_to_plot, dataset)

    fig.update_yaxes(title_text=title, automargin=True)

    html_file = tempfile.mktemp(suffix=".html", dir=CCD_Plugin.inst[id].tmp_dir)
    plotly.offline.plot(fig, filename=html_file, auto_open=False, config={'displaylogo': False})

    return html_file