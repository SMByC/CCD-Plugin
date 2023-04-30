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

"""
import tempfile
import numpy as np
import plotly
import plotly.graph_objects as go
import plotly.io as pio
from datetime import date, datetime


def generate_plot(ccd_results, dates, band_data, band_name, tmp_dir):
    dates_dt = np.array([date.fromordinal(d) for d in dates])
    band_data = np.array(band_data)

    mask = np.array(ccd_results['processing_mask'], dtype=bool)
    print('Start Date: {0}\nEnd Date: {1}\n'.format(dates_dt[0], dates_dt[-1]))

    predicted_values = []
    prediction_dates = []
    break_dates = []
    start_dates = []

    for num, result in enumerate(ccd_results['change_models']):
        print('Result: {}'.format(num))
        print('Start Date: {}'.format(date.fromordinal(result['start_day'])))
        print('End Date: {}'.format(date.fromordinal(result['end_day'])))
        print('Break Date: {}'.format(date.fromordinal(result['break_day'])), result['break_day'])
        print('QA: {}'.format(result['curve_qa']))
        print('Norm: {}\n'.format(np.linalg.norm([result['green']['magnitude'],
                                                  result['red']['magnitude'],
                                                  result['nir']['magnitude'],
                                                  result['swir1']['magnitude'],
                                                  result['swir2']['magnitude']])))
        print('Change prob: {}'.format(result['change_probability']))

        days = np.arange(result['start_day'], result['end_day'] + 1)
        prediction_dates.append(days)
        break_dates.append(result['break_day'])
        start_dates.append(result['start_day'])

        intercept = result[band_name.lower()]['intercept']
        coef = result[band_name.lower()]['coefficients']

        predicted_values.append(intercept + coef[0] * days +
                                coef[1] * np.cos(days * 1 * 2 * np.pi / 365.25) + coef[2] * np.sin(
            days * 1 * 2 * np.pi / 365.25) +
                                coef[3] * np.cos(days * 2 * 2 * np.pi / 365.25) + coef[4] * np.sin(
            days * 2 * 2 * np.pi / 365.25) +
                                coef[5] * np.cos(days * 3 * 2 * np.pi / 365.25) + coef[6] * np.sin(
            days * 3 * 2 * np.pi / 365.25))

    ######## plot with plotly ########

    pio.templates.default = "plotly_white"
    fig = go.Figure()

    # observed and masked values
    # fig.add_trace(go.Scatter(x=dates_dt[~mask], y=band_data[~mask], name='masked<br>values',
    #                          mode='markers', marker=dict(color='#bcbcbc', size=7, opacity=0.7)))  # , symbol="cross"
    fig.add_trace(go.Scatter(x=dates_dt[mask], y=band_data[mask], name='observed<br>values',
                             mode='markers', marker=dict(color='#4498d4', size=7, opacity=1)))  # , symbol="cross"

    # Predicted curves
    curve_colors = ["#56ad74", "#a291e1", "#c69255", "#e274cf", "#5ea5c5"]*2
    for idx, (_preddate, _predvalue) in enumerate(zip(prediction_dates, predicted_values)):
        fig.add_trace(go.Scatter(x=np.array([date.fromordinal(pd) for pd in _preddate]), y=_predvalue,
                                 name='predicted<br>values ({})'.format(idx + 1), opacity=0.6,
                                 hovertemplate="%{y}", line=dict(width=1.5, color=curve_colors[idx])))

    # break lines
    break_dates = list(set(start_dates+break_dates))  # delete duplicates
    for break_date in break_dates:
        fig.add_vline(x=datetime.fromordinal(break_date).timestamp() * 1000, line_width=1, line_dash="dash",
                      line_color="red", annotation_text=date.fromordinal(break_date).strftime("%Y-%m-%d"),
                      annotation_position="bottom left", annotation_textangle=-90, opacity=0.4,
                      annotation_font_size=9, annotation_font_color="red")

    # add a fake line to add the legend for the break lines
    fig.add_trace(go.Scatter(x=[dates_dt[0]]*2, y=[np.min(band_data)]*2, hoverinfo=None,
                             mode='lines', line=dict(color='red', width=1, dash='dash'), name='break lines'))

    # get longitude and latitude from CCD_PluginDockWidget
    from CCD_Plugin.CCD_Plugin import CCD_Plugin
    lon = CCD_Plugin.widget.longitude.value()
    lat = CCD_Plugin.widget.latitude.value()

    fig.update_layout(
        title={
            'text': "Lat: {} Lon: {}".format(lat, lon),
            'y': 0.98,
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top'},
        margin=go.layout.Margin(
            l=1,
            r=1,
            b=1,
            t=30,
            pad=0
        ),
        paper_bgcolor="white",
    )

    fig.update_traces(hovertemplate='%{y:.0f}<br>%{x}')
    fig.update_xaxes(title_text=None, tickangle=-90 if np.max(dates_dt).year - np.min(dates_dt).year > 20 else 0,
                     ticklabelmode="period", dtick="M12", tick0=date(np.min(dates_dt).year, 1, 1), automargin=True)

    if band_name in ['Blue', 'Green', 'Red', 'NIR', 'SWIR1', 'SWIR2']:
        title = "Surface Reflectance (x10⁴) - {}".format(band_name)
    if band_name in ["NBR", "NDVI", "EVI", "EVI2"]:
        title = "Index (x10⁴) - {}".format(band_name)
    if band_name in ["BRIGHTNESS", "GREENNESS", "WETNESS"]:
        title = "Index - {}".format(band_name)
    fig.update_yaxes(title_text=title, automargin=True)

    html_file = tempfile.mktemp(suffix=".html", dir=tmp_dir)
    plotly.offline.plot(fig, filename=html_file, auto_open=False, config={'displaylogo': False})

    return html_file
