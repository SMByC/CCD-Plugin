"""
/***************************************************************************
 CCD Plugin
                                 A QGIS plugin
 Continuous Change Detection Plugin
                              -------------------
        copyright            : (C) 2019-2026 by Xavier Corredor Llano, SMByC
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

from CCD_Plugin.core.plot import resolve_plot_style


def get_plugin_tmp_dir(id):
    """where the plugin writes its temporary files, read late: the plugins embedding the widget
    build it before setting tmp_dir"""
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    return CCD_Plugin.inst[id].tmp_dir


def get_plugin_config(id):
    """get the current configuration of the plugin"""
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    if id not in CCD_Plugin.inst or CCD_Plugin.inst[id].widget is None:
        return

    config = OrderedDict()

    # from the plugin widget
    config["lat"] = CCD_Plugin.inst[id].widget.latitude.value()
    config["lon"] = CCD_Plugin.inst[id].widget.longitude.value()
    config["dataset"] = CCD_Plugin.inst[id].widget.dataset.currentText()
    config["band_or_index_to_plot"] = CCD_Plugin.inst[id].widget.band_or_index_to_plot.currentText()
    config["plot_style"] = CCD_Plugin.inst[id].widget.plot_style.value
    config["breakpoint_bands"] = CCD_Plugin.inst[id].widget.box_breakpoint_bands.checkedItems()
    config["start_date"] = CCD_Plugin.inst[id].widget.start_date.date().toString("yyyy-MM-dd")
    config["end_date"] = CCD_Plugin.inst[id].widget.end_date.date().toString("yyyy-MM-dd")

    # from the advanced settings dialog
    adv = CCD_Plugin.inst[id].widget.advanced_settings
    config["start_doy"] = adv.start_doy.value() if adv.start_doy.isEnabled() else 1
    config["end_doy"] = adv.end_doy.value() if adv.end_doy.isEnabled() else 365
    config["num_obs"] = adv.num_obs.value()
    config["chi_square"] = adv.chi_square.value()
    config["min_years"] = adv.min_years.value()
    config["lambda_lasso"] = adv.lambda_lasso.value()
    config["cloud_filter"] = adv.cloud_filter.currentText()

    # other configurations
    config["auto_generate_plot"] = CCD_Plugin.inst[id].widget.auto_generate_plot.isChecked()

    return config


def restore_plugin_config(id, config):
    """restore the configuration of the plugin"""
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    if id not in CCD_Plugin.inst or CCD_Plugin.inst[id].widget is None or config is None:
        return False

    widget = CCD_Plugin.inst[id].widget
    resolved_style = resolve_plot_style(config.get("plot_style"), widget.plot_style)
    style_changed = resolved_style != widget.plot_style

    # from the plugin widget
    CCD_Plugin.inst[id].widget.latitude.setValue(config["lat"])
    CCD_Plugin.inst[id].widget.longitude.setValue(config["lon"])
    CCD_Plugin.inst[id].widget.dataset.setCurrentText(config["dataset"])
    CCD_Plugin.inst[id].widget.band_or_index_to_plot.setCurrentText(config["band_or_index_to_plot"])
    CCD_Plugin.inst[id].widget.box_breakpoint_bands.deselectAllOptions()
    CCD_Plugin.inst[id].widget.box_breakpoint_bands.setCheckedItems(config["breakpoint_bands"])
    CCD_Plugin.inst[id].widget.start_date.setDate(QDate.fromString(config["start_date"], "yyyy-MM-dd"))
    CCD_Plugin.inst[id].widget.end_date.setDate(QDate.fromString(config["end_date"], "yyyy-MM-dd"))

    # from the advanced settings dialog
    adv = CCD_Plugin.inst[id].widget.advanced_settings
    adv.start_doy.setValue(config["start_doy"])
    adv.end_doy.setValue(config["end_doy"])
    adv.num_obs.setValue(config["num_obs"])
    adv.chi_square.setValue(config["chi_square"])
    adv.min_years.setValue(config["min_years"])
    adv.lambda_lasso.setValue(config["lambda_lasso"])
    # optional: configurations saved before the cloud mask was selectable keep the default
    if "cloud_filter" in config:
        adv.cloud_filter.setCurrentText(config["cloud_filter"])

    # other configurations
    CCD_Plugin.inst[id].widget.auto_generate_plot.setChecked(config["auto_generate_plot"])

    if style_changed:
        widget.plot_style = resolved_style
    return style_changed
