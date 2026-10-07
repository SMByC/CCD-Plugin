import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import qgis.PyQt.QtCore as qt_core
except ImportError:
    QGIS_AVAILABLE = False
else:
    QGIS_AVAILABLE = qt_core.QDate is not None

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class _RecordingControl:
    """A widget control recording every value set on it; numeric ones carry their spin box range."""

    def __init__(self, name, events, minimum=None, maximum=None, text=""):
        self.name = name
        self.events = events
        self._minimum = minimum
        self._maximum = maximum
        self._text = text

    def _record(self, value):
        self.events.append((self.name, value))

    def minimum(self):
        return self._minimum

    def maximum(self):
        return self._maximum

    # the range of a QDateEdit, which clamps anything outside it
    def minimumDate(self):
        return qt_core.QDate(1752, 9, 14)

    def maximumDate(self):
        return qt_core.QDate(9999, 12, 31)

    def setValue(self, value):
        self._record(value)

    def setCurrentText(self, value):
        self._text = value
        self._record(value)

    def currentText(self):
        return self._text

    def deselectAllOptions(self):
        self._record(None)

    def setCheckedItems(self, value):
        self._record(value)

    def setDate(self, value):
        self._record(value.toString("yyyy-MM-dd"))

    def setChecked(self, value):
        self._record(value)

    def blockSignals(self, blocked):
        self.events.append((f"{self.name}.blockSignals", blocked))
        return not blocked


class _Widget:
    def __init__(self, style):
        self.events = []
        self._plot_style = style
        self.latitude = _RecordingControl("latitude", self.events, -90.0, 90.0)
        self.longitude = _RecordingControl("longitude", self.events, -180.0, 180.0)
        self.dataset = _RecordingControl("dataset", self.events)
        self.band_or_index_to_plot = _RecordingControl("band_or_index_to_plot", self.events, text="SWIR1")
        self.box_breakpoint_bands = _RecordingControl("box_breakpoint_bands", self.events)
        self.start_date = _RecordingControl("start_date", self.events)
        self.end_date = _RecordingControl("end_date", self.events)
        self.auto_generate_plot = _RecordingControl("auto_generate_plot", self.events)
        self.advanced_settings = types.SimpleNamespace(
            start_doy=_RecordingControl("start_doy", self.events, 1, 364),
            end_doy=_RecordingControl("end_doy", self.events, 2, 365),
            num_obs=_RecordingControl("num_obs", self.events, 1, 999),
            chi_square=_RecordingControl("chi_square", self.events, 0.0, 1.0),
            min_years=_RecordingControl("min_years", self.events, 0.0, 2.0),
            lambda_lasso=_RecordingControl("lambda_lasso", self.events, 0.0, 0.1),
            cloud_filter=_RecordingControl("cloud_filter", self.events),
        )

    @property
    def plot_style(self):
        return self._plot_style

    @plot_style.setter
    def plot_style(self, style):
        self._plot_style = style
        self.events.append(("plot_style", style))

    def settings_set(self):
        """Every value set on a control, leaving out the signal blocking around them."""
        return [event for event in self.events if not event[0].endswith(".blockSignals")]


class _PluginModule(types.ModuleType):
    CCD_Plugin: types.SimpleNamespace


def _complete_config(**overrides):
    config = {
        "lat": 5.0,
        "lon": -75.0,
        "dataset": "Landsat C2",
        "band_or_index_to_plot": "NIR",
        "breakpoint_bands": ["Green", "SWIR1"],
        "start_date": "2020-01-01",
        "end_date": "2021-01-01",
        "start_doy": 1,
        "end_doy": 365,
        "num_obs": 6,
        "chi_square": 0.99,
        "min_years": 1.33,
        "lambda_lasso": 0.002,
        "cloud_filter": "Cloud Score+",
        "auto_generate_plot": False,
    }
    config.update(overrides)
    return config


@unittest.skipUnless(QGIS_AVAILABLE, "QGIS Python bindings are required")
class RestorePluginConfigTest(unittest.TestCase):
    def _restore(self, widget, config, **kwargs):
        from CCD_Plugin.utils import config as config_module

        plugin_class = types.SimpleNamespace(inst={"test": types.SimpleNamespace(widget=widget)})
        plugin_module = _PluginModule("CCD_Plugin.CCD_Plugin")
        plugin_module.CCD_Plugin = plugin_class
        self.log = Mock()
        with (
            patch.dict(sys.modules, {"CCD_Plugin.CCD_Plugin": plugin_module}),
            patch.object(config_module.QgsMessageLog, "logMessage", self.log),
        ):
            return config_module.restore_plugin_config("test", config, **kwargs)

    def test_strict_restore_of_an_invalid_configuration_changes_nothing(self):
        from CCD_Plugin.core.plot import PlotStyle

        # Given: a configuration missing one setting and with others out of range.
        widget = _Widget(PlotStyle.LIGHT)
        config = _complete_config(
            plot_style="sepia", lat=95.0, lambda_lasso=20, cloud_filter="Mask clouds", start_date="1700-01-01"
        )
        del config["num_obs"]

        # When: it is restored from a file, strictly.
        with self.assertRaises(ValueError) as raised:
            self._restore(widget, config, strict=True)

        # Then: every problem is named - the date too, which the date control would have clamped
        # to 1752 - and not one control nor the style was touched.
        for setting in ("plot_style", "lat", "lambda_lasso", "cloud_filter", "num_obs", "start_date"):
            self.assertIn(setting, str(raised.exception))
        self.assertEqual(widget.events, [])
        self.assertIs(widget.plot_style, PlotStyle.LIGHT)

    def test_an_empty_configuration_file_is_reported(self):
        from CCD_Plugin.core.plot import PlotStyle

        # Given: an empty YAML file, which loads as None.
        # Then: a strict restore says so, while an embedding plugin passing None for a project saved
        # without a configuration simply restores nothing.
        widget = _Widget(PlotStyle.LIGHT)
        with self.assertRaisesRegex(ValueError, "empty"):
            self._restore(widget, None, strict=True)
        self.assertFalse(self._restore(widget, None))
        self.assertEqual(widget.events, [])

    def test_strict_restore_rejects_a_start_after_the_end(self):
        from CCD_Plugin.core.plot import PlotStyle

        widget = _Widget(PlotStyle.LIGHT)
        with self.assertRaisesRegex(ValueError, "start_date: after end_date"):
            self._restore(widget, _complete_config(start_date="2022-01-01"), strict=True)
        self.assertEqual(widget.events, [])

    def test_valid_explicit_style_is_committed_last_and_reports_change(self):
        from CCD_Plugin.core.plot import PlotStyle

        widget = _Widget(PlotStyle.LIGHT)

        style_changed = self._restore(widget, _complete_config(plot_style="dark"), strict=True)

        self.assertIs(widget.plot_style, PlotStyle.DARK)
        self.assertEqual((style_changed, widget.events[-1]), (True, ("plot_style", PlotStyle.DARK)))
        self.assertIn(("lambda_lasso", 0.002), widget.events)
        self.assertIn(("end_date", "2021-01-01"), widget.events)

    def test_the_band_combo_is_silent_while_the_configuration_is_applied(self):
        from CCD_Plugin.core.plot import PlotStyle

        # Given: a widget whose band combo repaints the plot when it changes.
        widget = _Widget(PlotStyle.LIGHT)

        # When: a configuration is restored.
        self._restore(widget, _complete_config())

        # Then: its signals are blocked before the first setting and released after the last, so
        # no repaint runs against a half-restored configuration.
        settings = [index for index, event in enumerate(widget.events) if not event[0].endswith(".blockSignals")]
        self.assertEqual(widget.events[0], ("band_or_index_to_plot.blockSignals", True))
        self.assertEqual(widget.events[-1], ("band_or_index_to_plot.blockSignals", False))
        self.assertLess(0, settings[0])

    def test_legacy_config_preserves_style_and_reports_no_change(self):
        from CCD_Plugin.core.plot import PlotStyle

        # Given: a configuration saved before the plot style and the cloud filter were settings.
        widget = _Widget(PlotStyle.DARK)
        config = _complete_config()
        del config["cloud_filter"]

        style_changed = self._restore(widget, config, strict=True)

        self.assertEqual((style_changed, widget.plot_style), (False, PlotStyle.DARK))
        self.assertNotIn("cloud_filter", [event[0] for event in widget.events])

    def test_lenient_restore_applies_what_is_valid_and_logs_the_rest(self):
        from CCD_Plugin.core.plot import PlotStyle

        # Given: a configuration an embedding plugin saved in its project long ago: a dataset name
        # of an older version, an unknown band, a lambda of the old 0-10000 scale.
        widget = _Widget(PlotStyle.LIGHT)
        config = _complete_config(dataset="Landsat col. 2", breakpoint_bands=["Green", "B4"], lambda_lasso=20)

        # When: it is restored the way those plugins do, without strict.
        style_changed = self._restore(widget, config)

        # Then: the project still opens: valid settings are applied, the legacy name is mapped,
        # and the invalid ones are logged and left as they were rather than clamped or raised.
        self.assertFalse(style_changed)
        settings = dict(widget.settings_set())
        self.assertEqual(settings["dataset"], "Landsat C2")
        self.assertEqual(settings["latitude"], 5.0)
        self.assertNotIn("lambda_lasso", settings)
        self.assertNotIn("box_breakpoint_bands", settings)
        self.log.assert_called_once()
        self.assertIn("lambda_lasso", self.log.call_args.args[0])

    def test_lenient_restore_leaves_the_date_range_alone_when_one_date_is_bad(self):
        from CCD_Plugin.core.plot import PlotStyle

        # Given: a damaged project configuration whose end date cannot be read.
        widget = _Widget(PlotStyle.LIGHT)

        # When: it is restored leniently - this raised KeyError half-way through.
        self._restore(widget, _complete_config(end_date="not a date"))

        # Then: the rest is applied and the range is left as it was, rather than half changed.
        settings = dict(widget.settings_set())
        self.assertEqual(settings["latitude"], 5.0)
        self.assertNotIn("start_date", settings)
        self.assertNotIn("end_date", settings)
        self.assertIn("start_date: not applied without a valid end_date", self.log.call_args.args[0])

    def test_yaml_restore_validates_strictly_and_repaints_only_when_needed(self):
        import yaml
        from CCD_Plugin.gui import CCD_Plugin_dockwidget as dockwidget_module

        restore_from_yaml = vars(dockwidget_module.CCD_PluginDockWidget.restore_plugin_from_yaml)["__wrapped__"]
        with tempfile.TemporaryDirectory() as temporary_directory:
            yaml_path = Path(temporary_directory) / "complete-config.yaml"
            yaml_path.write_text(yaml.safe_dump(_complete_config(plot_style="dark")), encoding="utf-8")

            for style_changed, expected_repaints in ((True, 1), (False, 0)):
                with self.subTest(style_changed=style_changed):
                    widget = types.SimpleNamespace(
                        id="test",
                        repaint_plot=Mock(),
                        band_or_index_to_plot=types.SimpleNamespace(currentText=lambda: "SWIR1"),
                    )
                    with (
                        patch.object(
                            dockwidget_module.QFileDialog,
                            "getOpenFileName",
                            return_value=(str(yaml_path), "YAML Files (*.yaml)"),
                        ),
                        patch.object(
                            dockwidget_module,
                            "restore_plugin_config",
                            return_value=style_changed,
                        ) as restore_config,
                    ):
                        restore_from_yaml(widget)

                    restore_config.assert_called_once_with("test", _complete_config(plot_style="dark"), strict=True)
                    self.assertEqual(widget.repaint_plot.call_count, expected_repaints)


if __name__ == "__main__":
    unittest.main()
