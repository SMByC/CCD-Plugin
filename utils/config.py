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
"""
from collections import OrderedDict

from qgis.PyQt.QtCore import QDate


def get_plugin_config(id):
    """get the current configuration of the plugin"""
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    if CCD_Plugin.inst[id].widget is None:
        return

    config = OrderedDict()

    # from the plugin widget
    config['lat'] = CCD_Plugin.inst[id].widget.latitude.value()
    config['lon'] = CCD_Plugin.inst[id].widget.longitude.value()
    config['dataset'] = CCD_Plugin.inst[id].widget.dataset.currentText()
    config['band_or_index'] = CCD_Plugin.inst[id].widget.band_or_index.currentText()
    config['breakpoint_bands'] = CCD_Plugin.inst[id].widget.box_breakpoint_bands.checkedItems()
    config['start_date'] = CCD_Plugin.inst[id].widget.start_date.date().toString("yyyy-MM-dd")
    config['end_date'] = CCD_Plugin.inst[id].widget.end_date.date().toString("yyyy-MM-dd")
    config['start_doy'] = CCD_Plugin.inst[id].widget.start_doy.value() if CCD_Plugin.inst[id].widget.start_doy.isEnabled() else 1
    config['end_doy'] = CCD_Plugin.inst[id].widget.end_doy.value() if CCD_Plugin.inst[id].widget.end_doy.isEnabled() else 365

    # from the advanced settings dialog
    config['num_obs'] = CCD_Plugin.inst[id].widget.advanced_settings.num_obs.value()
    config['chi_square'] = CCD_Plugin.inst[id].widget.advanced_settings.chi_square.value()
    config['min_years'] = CCD_Plugin.inst[id].widget.advanced_settings.min_years.value()
    config['lambda_lasso'] = CCD_Plugin.inst[id].widget.advanced_settings.lambda_lasso.value()

    # other configurations
    config['auto_generate_plot'] = CCD_Plugin.inst[id].widget.auto_generate_plot.isChecked()

    return config


def restore_plugin_config(id, config):
    """restore the configuration of the plugin"""
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    if CCD_Plugin.inst[id].widget is None or config is None:
        return

    # from the plugin widget
    CCD_Plugin.inst[id].widget.latitude.setValue(config['lat'])
    CCD_Plugin.inst[id].widget.longitude.setValue(config['lon'])
    CCD_Plugin.inst[id].widget.dataset.setCurrentText(config['dataset'])
    CCD_Plugin.inst[id].widget.band_or_index.setCurrentText(config['band_or_index'])
    CCD_Plugin.inst[id].widget.box_breakpoint_bands.deselectAllOptions()
    CCD_Plugin.inst[id].widget.box_breakpoint_bands.setCheckedItems(config['breakpoint_bands'])
    CCD_Plugin.inst[id].widget.start_date.setDate(QDate.fromString(config['start_date'], "yyyy-MM-dd"))
    CCD_Plugin.inst[id].widget.end_date.setDate(QDate.fromString(config['end_date'], "yyyy-MM-dd"))
    CCD_Plugin.inst[id].widget.start_doy.setValue(config['start_doy'])
    CCD_Plugin.inst[id].widget.end_doy.setValue(config['end_doy'])

    # from the advanced settings dialog
    CCD_Plugin.inst[id].widget.advanced_settings.num_obs.setValue(config['num_obs'])
    CCD_Plugin.inst[id].widget.advanced_settings.chi_square.setValue(config['chi_square'])
    CCD_Plugin.inst[id].widget.advanced_settings.min_years.setValue(config['min_years'])
    CCD_Plugin.inst[id].widget.advanced_settings.lambda_lasso.setValue(config['lambda_lasso'])

    # other configurations
    CCD_Plugin.inst[id].widget.auto_generate_plot.setChecked(config['auto_generate_plot'])
