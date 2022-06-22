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
import plotly.graph_objects as go
import plotly
from datetime import date, datetime


def generate_plot(ccd_results, dates, band_data, tmp_dir):
    dates_dt = np.array([date.fromordinal(d) for d in dates])
    band_data = np.array(band_data)

    mask = np.array(ccd_results['processing_mask'], dtype=np.bool)
    print(mask)
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

        intercept = result['swir1']['intercept']
        coef = result['swir1']['coefficients']

        predicted_values.append(intercept + coef[0] * days +
                                coef[1] * np.cos(days * 1 * 2 * np.pi / 365.25) + coef[2] * np.sin(
            days * 1 * 2 * np.pi / 365.25) +
                                coef[3] * np.cos(days * 2 * 2 * np.pi / 365.25) + coef[4] * np.sin(
            days * 2 * 2 * np.pi / 365.25) +
                                coef[5] * np.cos(days * 3 * 2 * np.pi / 365.25) + coef[6] * np.sin(
            days * 3 * 2 * np.pi / 365.25))

    ##### plot with plotly

    fig = go.Figure()

    # observed and masked values
    fig.add_trace(go.Scatter(x=dates_dt[~mask], y=band_data[~mask], name='masked<br>values',
                             mode='markers', marker=dict(color='#bcbcbc', size=7)))  # , symbol="cross"
    fig.add_trace(go.Scatter(x=dates_dt[mask], y=band_data[mask], name='observed<br>values',
                             mode='markers', marker=dict(color='#4596d3', size=7)))  # , symbol="cross"

    # Predicted curves
    for idx, (_preddate, _predvalue) in enumerate(zip(prediction_dates, predicted_values)):
        fig.add_trace(go.Scatter(x=np.array([date.fromordinal(pd) for pd in _preddate]), y=_predvalue,
                                 name='predicted<br>values ({})'.format(idx + 1),
                                 hovertemplate="%{y}", line=dict(width=2), opacity=0.5))

    # break lines
    break_dates = list(set(start_dates+break_dates))  # delete duplicates
    for break_date in break_dates:
        fig.add_vline(x=datetime.fromordinal(break_date).timestamp() * 1000, line_width=1, line_dash="dash",
                      line_color="red", annotation_text=date.fromordinal(break_date).strftime("%Y-%m-%d"),
                      annotation_position="bottom left", annotation_textangle=-90,
                      annotation_font_size=9, annotation_font_color="red")
    # add a fake line to add the legend for the break lines
    fig.add_trace(go.Scatter(x=[dates_dt[0]]*2, y=[np.min(band_data)]*2,
                             mode='lines', line=dict(color='red', width=1, dash='dash'), name='break lines'))

    fig.update_layout(
        margin=go.layout.Margin(
            l=0,
            r=0,
            b=0,
            t=25,
            pad=0
        ),
        paper_bgcolor="white",
    )

    fig.update_layout(hovermode=False)
    fig.update_xaxes(title_text="Time", tickangle=-90, ticklabelmode="period", dtick="M12",
                     tick0=date(np.min(dates_dt).year, 1, 1), automargin=True)
    fig.update_yaxes(title_text="Reflectance", automargin=True)

    html_file = tempfile.mktemp(suffix=".html", dir=tmp_dir)
    plotly.offline.plot(fig, filename=html_file, auto_open=False, config={'displaylogo': False})

    return html_file
