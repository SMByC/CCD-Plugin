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

import os
import tempfile
from collections import OrderedDict
from numbers import Real

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtCore import QDate

from CCD_Plugin.core.ccd_process import DATASET_AVAILABILITY
from CCD_Plugin.core.gee_common import CCD_BANDS
from CCD_Plugin.core.gee_data_sentinel import CLOUD_FILTERS
from CCD_Plugin.core.plot import PlotStyle

DATE_FORMAT = "yyyy-MM-dd"
DATASETS = tuple(DATASET_AVAILABILITY)
# Names older versions saved. Collection 1 was removed from the Earth Engine catalog, so a
# configuration made for it can only run on Collection 2.
LEGACY_DATASETS = {"Landsat col. 2": "Landsat C2", "Landsat C1": "Landsat C2", "Landsat col. 1": "Landsat C2"}


def get_plugin_tmp_dir(id):
    """where the plugin writes its temporary files, read late: the plugins embedding the widget
    build it before setting tmp_dir. Created when missing, so a plot is never written to the
    system temporary directory, where nothing would ever remove it."""
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    plugin = CCD_Plugin.inst[id]
    if not plugin.tmp_dir or not os.path.isdir(plugin.tmp_dir):
        created = getattr(plugin, "created_tmp_dir", None)
        if not created or not os.path.isdir(created):
            # recorded so it is removed with the instance; one an embedding plugin hands in stays its own
            created = plugin.created_tmp_dir = tempfile.mkdtemp(prefix="ccd_plugin_")
        plugin.tmp_dir = created
    return plugin.tmp_dir


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
    config["start_date"] = CCD_Plugin.inst[id].widget.start_date.date().toString(DATE_FORMAT)
    config["end_date"] = CCD_Plugin.inst[id].widget.end_date.date().toString(DATE_FORMAT)

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


def _number(value, control, integer=False):
    """`value` if it is a number the spin box `control` can hold, else raise ValueError.

    Checked here rather than left to the control, which clamps silently and so would run the
    computation with a parameter nobody asked for.
    """
    if isinstance(value, bool) or not isinstance(value, Real) or (integer and int(value) != value):
        raise ValueError(f"{value!r} is not {'an integer' if integer else 'a number'}")
    if not control.minimum() <= value <= control.maximum():
        raise ValueError(f"{value} is outside {control.minimum()}-{control.maximum()}")
    return int(value) if integer else float(value)


def _choice(value, choices, aliases=None):
    value = (aliases or {}).get(value, value)
    if value not in choices:
        raise ValueError(f"{value!r} is not one of {', '.join(choices)}")
    return value


def _date(value, control):
    date = QDate.fromString(str(value), DATE_FORMAT)
    if not date.isValid():
        raise ValueError(f"{value!r} is not a {DATE_FORMAT} date")
    # checked for the same reason as _number: the date control clamps silently
    if not control.minimumDate() <= date <= control.maximumDate():
        first, last = control.minimumDate().toString(DATE_FORMAT), control.maximumDate().toString(DATE_FORMAT)
        raise ValueError(f"{value} is outside {first} to {last}")
    return date


def _bands(value):
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise ValueError(f"{value!r} is not a list of bands")
    unknown = [band for band in value if band not in CCD_BANDS]
    if unknown:
        raise ValueError(f"unknown bands {', '.join(map(str, unknown))}")
    return list(value)


def _flag(value):
    if not isinstance(value, bool):
        raise ValueError(f"{value!r} is not true or false")
    return value


def validate_config(config, widget):
    """The settings of `config` checked against what `widget` accepts.

    Returns the usable values, normalised (legacy names mapped), and one message per field that
    is missing or invalid. Optional fields, absent from configurations saved by older versions,
    are simply left out.
    """
    if not isinstance(config, dict):
        return {}, ["the configuration is not a mapping of settings"]
    adv = widget.advanced_settings
    fields = {
        "lat": (True, lambda v: _number(v, widget.latitude)),
        "lon": (True, lambda v: _number(v, widget.longitude)),
        "dataset": (True, lambda v: _choice(v, DATASETS, LEGACY_DATASETS)),
        "band_or_index_to_plot": (True, lambda v: _choice(v, CCD_BANDS)),
        "plot_style": (False, lambda v: PlotStyle(v)),
        "breakpoint_bands": (True, _bands),
        "start_date": (True, lambda v: _date(v, widget.start_date)),
        "end_date": (True, lambda v: _date(v, widget.end_date)),
        "start_doy": (True, lambda v: _number(v, adv.start_doy, integer=True)),
        "end_doy": (True, lambda v: _number(v, adv.end_doy, integer=True)),
        "num_obs": (True, lambda v: _number(v, adv.num_obs, integer=True)),
        "chi_square": (True, lambda v: _number(v, adv.chi_square)),
        "min_years": (True, lambda v: _number(v, adv.min_years)),
        "lambda_lasso": (True, lambda v: _number(v, adv.lambda_lasso)),
        "cloud_filter": (False, lambda v: _choice(v, CLOUD_FILTERS)),
        "auto_generate_plot": (False, _flag),
    }
    values, problems = {}, []
    for name, (required, parse) in fields.items():
        if name not in config or config[name] is None:
            if required:
                problems.append(f"{name}: missing")
            continue
        try:
            values[name] = parse(config[name])
        except ValueError as error:
            problems.append(f"{name}: {error}")
    # the dates are a range: one without the other would leave it half old, half new
    dates = [name for name in ("start_date", "end_date") if name in values]
    if len(dates) == 1:
        missing = "end_date" if dates == ["start_date"] else "start_date"
        problems.append(f"{dates[0]}: not applied without a valid {missing}")
        del values[dates[0]]
    elif dates and values["start_date"] > values["end_date"]:
        problems.append("start_date: after end_date")
        del values["start_date"], values["end_date"]
    return values, problems


def restore_plugin_config(id, config, strict=False):
    """Restore the configuration of the plugin; True when the plot style changed.

    strict: refuse the whole configuration, changing nothing, if any setting is missing or
    invalid (ValueError) - what a configuration file restore wants. Otherwise every valid setting
    is applied and the rest is reported to the log and left as it is: the plugins embedding the
    widget restore configurations saved in their own project files, sometimes by older versions,
    and must still open.
    """
    from CCD_Plugin.CCD_Plugin import CCD_Plugin

    if id not in CCD_Plugin.inst or CCD_Plugin.inst[id].widget is None:
        return False
    if config is None:
        # an empty file; the embedding plugins pass None for a project saved without a configuration
        if strict:
            raise ValueError("The configuration is empty.")
        return False

    widget = CCD_Plugin.inst[id].widget
    values, problems = validate_config(config, widget)
    if problems:
        message = "Invalid CCD-Plugin configuration: " + "; ".join(problems)
        if strict:
            raise ValueError(message)
        QgsMessageLog.logMessage(
            message + ". Those settings were left as they were.", "CCD-Plugin", Qgis.MessageLevel.Warning
        )

    # The band combo repaints on currentIndexChanged; left connected, that repaint would run
    # against a half-restored configuration. The caller repaints once the restore is complete.
    widget.band_or_index_to_plot.blockSignals(True)
    try:
        if "lat" in values:
            widget.latitude.setValue(values["lat"])
        if "lon" in values:
            widget.longitude.setValue(values["lon"])
        if "dataset" in values:
            widget.dataset.setCurrentText(values["dataset"])
        if "band_or_index_to_plot" in values:
            widget.band_or_index_to_plot.setCurrentText(values["band_or_index_to_plot"])
        if "breakpoint_bands" in values:
            widget.box_breakpoint_bands.deselectAllOptions()
            widget.box_breakpoint_bands.setCheckedItems(values["breakpoint_bands"])
        if "start_date" in values:
            widget.start_date.setDate(values["start_date"])
            widget.end_date.setDate(values["end_date"])

        # from the advanced settings dialog
        adv = widget.advanced_settings
        for name in ("start_doy", "end_doy", "num_obs", "chi_square", "min_years", "lambda_lasso"):
            if name in values:
                getattr(adv, name).setValue(values[name])
        if "cloud_filter" in values:
            adv.cloud_filter.setCurrentText(values["cloud_filter"])

        # other configurations
        if "auto_generate_plot" in values:
            widget.auto_generate_plot.setChecked(values["auto_generate_plot"])
    finally:
        widget.band_or_index_to_plot.blockSignals(False)

    resolved_style = values.get("plot_style", widget.plot_style)
    style_changed = resolved_style != widget.plot_style
    if style_changed:
        widget.plot_style = resolved_style
    return style_changed
