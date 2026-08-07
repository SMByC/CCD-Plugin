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
    def __init__(self, name, events):
        self.name = name
        self.events = events

    def _record(self, value):
        self.events.append((self.name, value))

    def setValue(self, value):
        self._record(value)

    def setCurrentText(self, value):
        self._record(value)

    def deselectAllOptions(self):
        self._record(None)

    def setCheckedItems(self, value):
        self._record(value)

    def setDate(self, value):
        self._record(value)

    def setChecked(self, value):
        self._record(value)


class _Widget:
    def __init__(self, style):
        self.events = []
        self._plot_style = style
        self.latitude = _RecordingControl("latitude", self.events)
        self.longitude = _RecordingControl("longitude", self.events)
        self.dataset = _RecordingControl("dataset", self.events)
        self.band_or_index_to_plot = _RecordingControl("band_or_index_to_plot", self.events)
        self.box_breakpoint_bands = _RecordingControl("box_breakpoint_bands", self.events)
        self.start_date = _RecordingControl("start_date", self.events)
        self.end_date = _RecordingControl("end_date", self.events)
        self.auto_generate_plot = _RecordingControl("auto_generate_plot", self.events)
        self.advanced_settings = types.SimpleNamespace(
            start_doy=_RecordingControl("start_doy", self.events),
            end_doy=_RecordingControl("end_doy", self.events),
            num_obs=_RecordingControl("num_obs", self.events),
            chi_square=_RecordingControl("chi_square", self.events),
            min_years=_RecordingControl("min_years", self.events),
            lambda_lasso=_RecordingControl("lambda_lasso", self.events),
            cloud_filter=_RecordingControl("cloud_filter", self.events),
        )

    @property
    def plot_style(self):
        return self._plot_style

    @plot_style.setter
    def plot_style(self, style):
        self._plot_style = style
        self.events.append(("plot_style", style))


class _PluginModule(types.ModuleType):
    CCD_Plugin: types.SimpleNamespace


def _complete_config(**overrides):
    config = {
        "lat": 5.0,
        "lon": -75.0,
        "dataset": "Landsat",
        "band_or_index_to_plot": "B4",
        "breakpoint_bands": ["B4"],
        "start_date": "2020-01-01",
        "end_date": "2021-01-01",
        "start_doy": 1,
        "end_doy": 365,
        "num_obs": 6,
        "chi_square": 0.99,
        "min_years": 1.33,
        "lambda_lasso": 20,
        "cloud_filter": "Mask clouds",
        "auto_generate_plot": False,
    }
    config.update(overrides)
    return config


@unittest.skipUnless(QGIS_AVAILABLE, "QGIS Python bindings are required")
class RestorePluginConfigTest(unittest.TestCase):
    def _restore(self, widget, config):
        from CCD_Plugin.utils.config import restore_plugin_config

        plugin_class = types.SimpleNamespace(inst={"test": types.SimpleNamespace(widget=widget)})
        plugin_module = _PluginModule("CCD_Plugin.CCD_Plugin")
        plugin_module.CCD_Plugin = plugin_class
        with patch.dict(sys.modules, {"CCD_Plugin.CCD_Plugin": plugin_module}):
            return restore_plugin_config("test", config)

    def test_incomplete_explicit_style_does_not_mutate_current_style(self):
        from CCD_Plugin.core.plot import PlotStyle

        widget = _Widget(PlotStyle.LIGHT)
        config = _complete_config(plot_style="dark")
        del config["auto_generate_plot"]

        with self.assertRaises(KeyError):
            self._restore(widget, config)

        self.assertIs(widget.plot_style, PlotStyle.LIGHT)

    def test_valid_explicit_style_is_committed_last_and_reports_change(self):
        from CCD_Plugin.core.plot import PlotStyle

        widget = _Widget(PlotStyle.LIGHT)

        style_changed = self._restore(widget, _complete_config(plot_style="dark"))

        self.assertIs(widget.plot_style, PlotStyle.DARK)
        self.assertEqual((style_changed, widget.events[-1]), (True, ("plot_style", PlotStyle.DARK)))

    def test_legacy_config_preserves_style_and_reports_no_change(self):
        from CCD_Plugin.core.plot import PlotStyle

        widget = _Widget(PlotStyle.DARK)

        style_changed = self._restore(widget, _complete_config())

        self.assertEqual((style_changed, widget.plot_style), (False, PlotStyle.DARK))

    def test_yaml_restore_repaints_only_when_style_changes(self):
        import yaml
        from CCD_Plugin.gui import CCD_Plugin_dockwidget as dockwidget_module

        restore_from_yaml = vars(dockwidget_module.CCD_PluginDockWidget.restore_plugin_from_yaml)["__wrapped__"]
        with tempfile.TemporaryDirectory() as temporary_directory:
            yaml_path = Path(temporary_directory) / "complete-config.yaml"
            yaml_path.write_text(yaml.safe_dump(_complete_config(plot_style="dark")), encoding="utf-8")

            for style_changed, expected_repaints in ((True, 1), (False, 0)):
                with self.subTest(style_changed=style_changed):
                    widget = types.SimpleNamespace(id="test", repaint_plot=Mock())
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

                    restore_config.assert_called_once_with("test", _complete_config(plot_style="dark"))
                    self.assertEqual(widget.repaint_plot.call_count, expected_repaints)


if __name__ == "__main__":
    unittest.main()
