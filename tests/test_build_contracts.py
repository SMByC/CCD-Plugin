import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BuildContractTest(unittest.TestCase):
    def test_requirements_is_the_only_runtime_dependency_source(self):
        # Given: the project metadata and runtime requirements declaration.
        pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

        # When/Then: Plotly is unpinned in the sole runtime source and lockfiles stay absent.
        self.assertEqual(requirements.splitlines(), ["plotly"])
        self.assertNotIn("dependencies", pyproject["project"])
        self.assertFalse((PROJECT_ROOT / "uv.lock").exists())
        self.assertIn("uv.lock", gitignore.splitlines())

    def test_make_uses_lockless_uv_without_legacy_upload_or_digest(self):
        # Given: the non-GUI automation surface.
        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

        # When/Then: tests and extlibs bypass project locking without superseded release code.
        self.assertIn(
            "uv run --no-project --with-requirements requirements.txt --with numpy python -m unittest", makefile
        )
        self.assertIn("uv pip install --target=extlibs -r requirements.txt", makefile)
        for forbidden in ("uv export", "--frozen", "--require-hashes", ".extlibs-requirements.txt", "uv.lock"):
            self.assertNotIn(forbidden, makefile)
        self.assertNotIn("plugin_upload", makefile)
        self.assertNotRegex(makefile, r"(?m)^(?:upload|package):")
        self.assertNotIn("extlibs-sha256", makefile)
        self.assertNotIn("extlibs_security", makefile)
        self.assertNotIn("lrelease-qt4", makefile)

    def test_package_contains_screenshot_without_digest_manifest(self):
        # Given: the package source contract.
        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

        # When/Then: user-facing artwork remains and digest metadata is gone.
        self.assertRegex(makefile, r"(?m)^EXTRAS = .*screenshot\.webp$")
        self.assertTrue((PROJECT_ROOT / "screenshot.webp").is_file())
        self.assertFalse((PROJECT_ROOT / "extlibs-sha256.json").exists())

    def test_qgis_plugin_ci_uses_nested_plugin_layout(self):
        # Given: the repository packaging configuration.
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        # When/Then: qgis-plugin-ci discovers the staged nested plugin and repository.
        self.assertIn("[tool.qgis-plugin-ci]", pyproject)
        self.assertIn('plugin_path = "CCD_Plugin"', pyproject)
        self.assertIn('github_organization_slug = "SMByC"', pyproject)
        self.assertIn('project_slug = "CCD-Plugin"', pyproject)

    def test_stage_script_copies_only_release_sources(self):
        # Given: a temporary staging repository root.
        with tempfile.TemporaryDirectory() as temporary_directory:
            stage = Path(temporary_directory)

            # When: the reusable staging script copies the current working tree.
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "stage_plugin.py"), str(stage)],
                cwd=PROJECT_ROOT,
                check=True,
            )

            # Then: qgis-plugin-ci config is at root and runtime sources are nested exactly once.
            plugin = stage / "CCD_Plugin"
            self.assertTrue((stage / "pyproject.toml").is_file())
            self.assertTrue((plugin / "metadata.txt").is_file())
            self.assertTrue((plugin / "resources.py").is_file())
            self.assertFalse((plugin / "resources.qrc").exists())
            self.assertFalse((plugin / "tests").exists())
            self.assertFalse((plugin / "pyproject.toml").exists())
            self.assertFalse((plugin / "extlibs.zip").exists())
            self.assertFalse((plugin / "CCD_Plugin").exists())

    def test_stage_script_requires_destination_argument(self):
        # Given: the staging command without its required destination.
        command = [sys.executable, str(PROJECT_ROOT / "scripts" / "stage_plugin.py")]

        # When: the CLI parses the incomplete invocation.
        result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)

        # Then: it returns argparse's clean usage error instead of a traceback.
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_extlibs_stamps_installed_plotly_version_before_cleanup(self):
        # Given: the extlibs build recipe.
        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

        # When/Then: Plotly keeps its installed version after packaging metadata is removed.
        version_capture = 'from importlib.metadata import version; print(version(\'plotly\'))'
        self.assertIn(version_capture, makefile)
        self.assertIn('__version__ = \\"$${PLOTLY_VERSION}\\"', makefile)
        self.assertLess(makefile.index(version_capture), makefile.index('-name "*.dist-info"'))
        self.assertIn('-name "*.dist-info"', makefile)
        self.assertIn('-name "*.egg-info"', makefile)
        self.assertIn('-name ".lock"', makefile)
        self.assertIn(".PHONY: extlibs", makefile)
        self.assertNotIn("6.7.0", makefile)
        self.assertIn("import plotly, plotly.graph_objects; assert plotly.__version__", makefile)

    def test_workflows_build_plugin_and_extlibs_artifacts(self):
        # Given: manual and tagged release workflows.
        package = (PROJECT_ROOT / ".github" / "workflows" / "package.yml").read_text(encoding="utf-8")
        release = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        # When/Then: both use the pinned isolated CLI without dependency caching.
        for workflow in (package, release):
            self.assertIn("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2", workflow)
            self.assertIn("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0", workflow)
            self.assertIn("astral-sh/setup-uv@e92bafb6253dcd438e0484186d7669ea7a8ca1cc # v6.4.3", workflow)
            self.assertIn("qgis-plugin-ci==2.10.0", workflow)
            self.assertIn('python-version: "3.12"', workflow)
            self.assertNotIn("enable-cache", workflow)
            self.assertNotIn("cache-dependency-glob", workflow)
            self.assertNotIn("uv.lock", workflow)
            self.assertIn("make extlibs", workflow)
            self.assertIn("scripts/stage_plugin.py", workflow)
            self.assertIn("resources.py", workflow)
            self.assertIn("resources_rc", workflow)
            self.assertIn("^[0-9]+\\.[0-9]+(\\.[0-9]+)?$", workflow)
            self.assertNotIn("! unzip -l", workflow)

        self.assertIn("workflow_dispatch:", package)
        self.assertIn("required: true", package)
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2", package)
        self.assertIn("extlibs.zip", package)
        self.assertIn("tags:", release)
        self.assertIn("contents: write", release)
        self.assertIn('gh release create "$RELEASE_TAG"', release)
        self.assertIn("--draft", release)
        self.assertIn('gh release upload "$RELEASE_TAG" extlibs.zip', release)
        self.assertIn('gh release edit "$RELEASE_TAG" --draft=false', release)
        self.assertLess(release.index("--draft"), release.index("qgis-plugin-ci release"))
        self.assertLess(release.index("qgis-plugin-ci release"), release.index("gh release upload"))
        self.assertLess(release.index("gh release upload"), release.index("gh release edit"))
        self.assertIn("QGIS_TOKEN", release)
        self.assertIn("OSGEO_USERNAME", release)
        self.assertIn("OSGEO_PASSWORD", release)
        self.assertIn('if [ -n "$QGIS_TOKEN" ]; then', release)
        self.assertIn('elif [ -n "$OSGEO_USERNAME" ] && [ -n "$OSGEO_PASSWORD" ]; then', release)
        self.assertIn('elif [ -n "$OSGEO_USERNAME" ] || [ -n "$OSGEO_PASSWORD" ]; then', release)

    def test_unload_removes_global_coordinate_marker(self):
        # Given: the plugin lifecycle source.
        source = (PROJECT_ROOT / "CCD_Plugin.py").read_text(encoding="utf-8")
        unload_start = source.index("    def unload(self):")
        unload_end = source.index("    def removes_temporary_files(self):")
        unload_source = source[unload_start:unload_end]

        # When/Then: unload performs the same global marker cleanup as dock close.
        self.assertIn("PickerCoordsOnMap.delete_markers()", unload_source)

    def test_completed_plot_coordinates_come_from_completed_config(self):
        # Given: the task completion source.
        source = (PROJECT_ROOT / "gui" / "CCD_Plugin_dockwidget.py").read_text(encoding="utf-8")
        completion = source[source.index("    def ccd_completed(") : source.index("    @wait_process")]

        # When/Then: mutable coordinate widgets are not consulted after computation.
        self.assertIn('longitude=float(config["lon"])', completion)
        self.assertIn('latitude=float(config["lat"])', completion)
        self.assertNotIn("self.longitude.value()", completion)
        self.assertNotIn("self.latitude.value()", completion)

    def test_failure_preserves_confirmed_plot_state(self):
        # Given: the task completion source.
        source = (PROJECT_ROOT / "gui" / "CCD_Plugin_dockwidget.py").read_text(encoding="utf-8")
        completion = source[source.index("    def ccd_completed(") : source.index("    @wait_process")]

        # When/Then: failure reloads the active plot and only clears last_config when none exists.
        self.assertIn("active = self.plot_files.active_path", completion)
        self.assertIn("self.plot_webview.load(QUrl.fromLocalFile(str(active)))", completion)
        self.assertIn("self.last_config = None", completion)
        self.assertNotIn('self.plot_webview.setHtml("")', completion)
        self.assertIn("if task.isCanceled():", completion)
        self.assertIn("level = Qgis.MessageLevel.Info", completion)

    def test_task_start_always_replaces_view_with_loading_but_cached_repaint_does_not(self):
        # Given: the QGIS-independent source contract for task starts and cached repaints.
        source = (PROJECT_ROOT / "gui" / "CCD_Plugin_dockwidget.py").read_text(encoding="utf-8")
        task_start_index = source.index("    def start_ccd_task(")
        repaint_index = source.index("    def repaint_plot(")
        task_start = source[task_start_index : source.index("    @staticmethod", task_start_index)]
        repaint = source[repaint_index : source.index("    @error_handler", repaint_index)]

        # When/Then: every real task start shows loading after cleanup without clearing the active file.
        clean_position = task_start.index("self.clean_plot()")
        loading_position = task_start.index("self.plot_webview.setHtml(loading_page_html(self.plot_style))")
        self.assertLess(clean_position, loading_position)
        self.assertNotIn("active_path", task_start)
        self.assertNotIn("plot_files.clear", task_start)

        # And: the cache-hit repaint path never renders the loading page directly.
        self.assertNotIn("loading_page_html", repaint)
        self.assertIn("self.start_ccd_task(config)", repaint)
