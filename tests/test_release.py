import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import publish_release
from scripts.publish_release import publish_github, publish_qgis, validate_archives

VERSION = "26.10"
REPOSITORY = "SMByC/CCD-Plugin"
METADATA = (
    f"[general]\nname=CCD-Plugin\nversion={VERSION}\nqgisMinimumVersion=4.0\n"
    "changelog=Release notes\nexperimental=False\n"
)


class GithubService:
    """The gh boundary, retaining remote state across failed attempts and workflow reruns."""

    def __init__(self):
        self.exists = False
        self.draft = True
        self.prerelease = False
        self.assets = {}
        self.asset_ids = {}
        self.starters = set()
        self.calls = []
        self.creations = 0
        self.fail_upload = False
        self.fail_archive = None
        self.fail_lookup = False
        self.fail_publish = False

    def run(self, command, **kwargs):
        self.calls.append(command)
        args = command[1:]
        output = ""
        if args[:2] == ["api", f"repos/{REPOSITORY}/releases"]:
            if self.fail_lookup:
                raise subprocess.CalledProcessError(1, command)
            if self.exists:
                release = {
                    "id": 42,
                    "draft": self.draft,
                    "prerelease": self.prerelease,
                    "assets": [
                        {
                            "id": self.asset_ids.setdefault(name, len(self.asset_ids) + 1),
                            "name": name,
                            "state": "starter" if name in self.starters else "uploaded",
                            "size": len(contents),
                        }
                        for name, contents in self.assets.items()
                    ],
                }
                output = "42\n" if args[-1].endswith("| .id") else json.dumps(release) + "\n"
        elif args[:2] == ["release", "create"]:
            if self.exists:
                raise subprocess.CalledProcessError(1, command)
            self.exists = True
            self.creations += 1
            self.draft = "--draft" in args
        elif args[:2] == ["release", "upload"]:
            for filename in args[3 : args.index("--repo")]:
                path = Path(filename)
                if path.name in self.assets and "--clobber" not in args:
                    raise subprocess.CalledProcessError(1, command)
                # gh deletes a clobbered asset before upload. A failed upload can leave an empty
                # starter record, not the completed replacement the old fake incorrectly stored.
                self.assets.pop(path.name, None)
                self.starters.discard(path.name)
                if self.fail_upload or self.fail_archive == path.name:
                    self.assets[path.name] = b""
                    self.starters.add(path.name)
                    raise subprocess.CalledProcessError(1, command)
                self.assets[path.name] = path.read_bytes()
        elif args[0] == "api" and args[1].startswith(f"repos/{REPOSITORY}/releases/assets/"):
            self.assert_delete(command)
        elif args[:2] == ["api", f"repos/{REPOSITORY}/releases/42"]:
            if self.fail_publish:
                raise subprocess.CalledProcessError(1, command)
            self.draft = False
        else:
            raise AssertionError(f"Unexpected gh invocation: {command}")
        return subprocess.CompletedProcess(command, 0, stdout=output)

    def assert_delete(self, command):
        if command[-2:] != ["--method", "DELETE"]:
            raise AssertionError(command)
        asset_id = int(command[2].rsplit("/", 1)[1])
        name = next(name for name, number in self.asset_ids.items() if number == asset_id)
        del self.assets[name]
        self.starters.discard(name)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plugin = self.root / f"CCD_Plugin.{VERSION}.zip"
        self.extlibs = self.root / "extlibs.zip"
        self.notes = self.root / "notes.md"
        self.notes.write_text("Release notes", encoding="utf-8")
        self.write_plugin()
        with zipfile.ZipFile(self.extlibs, "w") as archive:
            archive.writestr("plotly/__init__.py", '__version__ = "7.1.0"')
            archive.writestr("plotly/package_data/plotly.min.js", "// fixture")

        self.github = GithubService()
        patcher = patch("scripts.publish_release.subprocess.run", side_effect=self.github.run)
        patcher.start()
        self.addCleanup(patcher.stop)

        upload_module = types.ModuleType("qgispluginci.release")
        self.token_upload = upload_module.upload_plugin_to_osgeo_with_token = Mock()
        self.password_upload = upload_module.upload_plugin_to_osgeo_xml_rpc = Mock()
        patcher = patch.dict(sys.modules, {"qgispluginci.release": upload_module})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {"QGIS_TOKEN": "", "OSGEO_USERNAME": "", "OSGEO_PASSWORD": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_plugin(self, metadata=METADATA, extra=None):
        files = {
            "CCD_Plugin/__init__.py": "",
            "CCD_Plugin/metadata.txt": metadata,
            "CCD_Plugin/resources.py": "from qgis.PyQt import QtCore\n",
            "CCD_Plugin/screenshot.webp": "fixture",
        }
        files.update(extra or {})
        with zipfile.ZipFile(self.plugin, "w") as archive:
            for name, contents in files.items():
                archive.writestr(name, contents)

    def publish(self):
        publish_github(REPOSITORY, VERSION, self.plugin, self.extlibs, self.notes)

    def upload_qgis(self):
        publish_qgis(self.plugin, repository=REPOSITORY, version=VERSION)

    def assert_complete_release(self):
        self.assertFalse(self.github.draft)
        self.assertEqual(self.github.assets[self.plugin.name], self.plugin.read_bytes())
        self.assertEqual(self.github.assets[self.extlibs.name], self.extlibs.read_bytes())

    def test_invalid_archive_cannot_create_or_change_a_remote_release(self):
        for metadata, extra in (
            (METADATA.replace(VERSION, "26.8"), None),
            (METADATA.replace("Release notes", "1% fewer observations"), None),
            (METADATA, {"CCD_Plugin/tests/test_leaked.py": ""}),
        ):
            with self.subTest(metadata=metadata, extra=extra):
                self.write_plugin(metadata, extra)
                with self.assertRaises(ValueError):
                    self.publish()
                self.assertEqual(self.github.calls, [])

    def test_missing_dependencies_cannot_publish_a_release(self):
        self.extlibs.unlink()
        with self.assertRaises(FileNotFoundError):
            self.publish()
        self.assertEqual(self.github.calls, [])

    def test_incomplete_dependency_archive_is_rejected(self):
        with zipfile.ZipFile(self.extlibs, "w") as archive:
            archive.writestr("plotly/__init__.py", "")
        with self.assertRaisesRegex(ValueError, "Dependency archive"):
            validate_archives(VERSION, self.plugin, self.extlibs)

    def test_partial_upload_stays_draft_and_retry_replaces_existing_assets(self):
        self.github.fail_upload = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertTrue(self.github.draft)
        self.assertEqual(set(self.github.assets), {self.extlibs.name})

        self.github.fail_upload = False
        self.publish()

        self.assertEqual(self.github.creations, 1)
        self.assert_complete_release()

    def test_failed_publish_can_be_retried_with_both_assets_already_uploaded(self):
        self.github.fail_publish = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertTrue(self.github.draft)
        self.assertEqual(len(self.github.assets), 2)

        self.github.fail_publish = False
        self.publish()
        self.assert_complete_release()
        self.assertEqual(self.github.creations, 1)

    def test_existing_public_release_can_be_repaired_and_rerun(self):
        self.github.exists, self.github.draft = True, False
        self.github.assets[self.plugin.name] = self.plugin.read_bytes()
        self.publish()
        self.publish()
        self.assert_complete_release()
        self.assertEqual(self.github.creations, 0)

    def test_public_rerun_does_not_delete_working_assets_even_if_uploads_fail(self):
        self.publish()
        published = self.github.assets.copy()
        self.github.calls.clear()
        self.github.fail_upload = True

        self.publish()

        self.assertEqual(self.github.assets, published)
        self.assertFalse(self.github.draft)
        self.assertFalse(any(call[1:3] == ["release", "upload"] for call in self.github.calls))
        self.assertFalse(any("PATCH" in call or "DELETE" in call for call in self.github.calls))

    def test_public_rerun_preserves_published_bytes_when_a_rebuild_differs(self):
        self.publish()
        published = self.github.assets.copy()
        self.write_plugin(METADATA.replace("Release notes", "Rebuilt notes"))

        self.publish()

        self.assertEqual(self.github.assets, published)

    def test_public_repair_retries_a_failed_missing_asset_without_touching_the_other(self):
        self.github.exists, self.github.draft = True, False
        self.github.assets[self.plugin.name] = self.plugin.read_bytes()
        self.github.fail_archive = self.extlibs.name
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(self.github.assets[self.plugin.name], self.plugin.read_bytes())

        self.github.fail_archive = None
        self.publish()

        self.assert_complete_release()
        uploads = [call for call in self.github.calls if call[1:3] == ["release", "upload"]]
        self.assertTrue(all("--clobber" not in call for call in uploads))
        self.assertTrue(all(str(self.plugin) not in call for call in uploads))

    def test_github_prerelease_rejects_a_nonexperimental_plugin_archive(self):
        self.github.exists = True
        self.github.prerelease = True

        with self.assertRaisesRegex(ValueError, "experimental"):
            self.publish()

        self.assertEqual(self.github.assets, {})
        self.assertTrue(self.github.draft)

    def test_prepare_marks_prereleases_experimental_before_packaging(self):
        self.github.exists, self.github.prerelease = True, True
        staged = self.root / "metadata.txt"
        staged.write_text(METADATA, encoding="utf-8")

        publish_release.prepare_metadata(REPOSITORY, VERSION, staged)

        text = staged.read_text(encoding="utf-8")
        self.assertIn("experimental=True", text)
        self.assertIn("qgisMinimumVersion=4.0", text)
        self.assertIn(f"version={VERSION}", text)
        self.write_plugin(text)
        self.publish()
        self.assert_complete_release()
        self.assertTrue(self.github.prerelease)

    def test_prepare_preserves_explicit_experimental_metadata_for_stable_releases(self):
        for exists in (False, True):
            with self.subTest(existing_release=exists):
                self.github.exists = exists
                staged = self.root / "metadata.txt"
                text = METADATA.replace("experimental=False", "experimental=True")
                staged.write_text(text, encoding="utf-8")

                publish_release.prepare_metadata(REPOSITORY, VERSION, staged)

                self.assertEqual(staged.read_text(encoding="utf-8"), text)

    def test_prepare_rejects_a_mismatched_staged_version(self):
        staged = self.root / "metadata.txt"
        text = METADATA.replace(VERSION, "26.8")
        staged.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "version"):
            publish_release.prepare_metadata(REPOSITORY, VERSION, staged)
        self.assertEqual(staged.read_text(encoding="utf-8"), text)

    def test_publication_never_replaces_an_unexpected_incomplete_public_asset(self):
        self.github.exists, self.github.draft = True, False
        self.github.assets[self.extlibs.name] = b""  # reported uploaded, not a failed starter
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.publish()
        self.assertEqual(self.github.assets, {self.extlibs.name: b""})
        self.assertFalse(any("DELETE" in call or "--clobber" in call for call in self.github.calls))

    def test_lookup_failure_does_not_create_another_release(self):
        self.github.fail_lookup = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(self.github.creations, 0)
        self.assertEqual(self.github.assets, {})

    def test_qgis_failure_leaves_complete_github_assets_and_allows_retry(self):
        self.publish()
        with patch.dict(os.environ, {"QGIS_TOKEN": "test-token"}):
            self.token_upload.side_effect = SystemExit(2)
            with self.assertRaises(SystemExit):
                self.upload_qgis()
            self.assert_complete_release()

            # A full workflow rerun reuses the completed public release without replacing assets.
            self.publish()
            self.token_upload.side_effect = None
            self.upload_qgis()
        self.assert_complete_release()
        self.token_upload.assert_called_with(archive=str(self.plugin), package_name="CCD_Plugin", token="test-token")
        self.assertEqual(self.github.creations, 1)

    def test_qgis_publication_uses_the_built_archive_without_mutating_github(self):
        self.github.exists, self.github.draft = True, False
        with patch.dict(os.environ, {"OSGEO_USERNAME": "test-user", "OSGEO_PASSWORD": "test-password"}):
            self.upload_qgis()
        self.password_upload.assert_called_once_with(
            username="test-user", password="test-password", archive=str(self.plugin)
        )
        self.assertTrue(all(call[1:3] == ["api", f"repos/{REPOSITORY}/releases"] for call in self.github.calls))

    def test_qgis_refuses_a_stable_archive_after_the_release_is_marked_prerelease(self):
        self.publish()
        self.github.prerelease = True
        with patch.dict(os.environ, {"QGIS_TOKEN": "test-token"}), self.assertRaisesRegex(ValueError, "experimental"):
            self.upload_qgis()
        self.token_upload.assert_not_called()

    def test_qgis_accepts_an_experimental_archive_for_a_prerelease(self):
        self.github.exists, self.github.draft, self.github.prerelease = True, False, True
        self.write_plugin(METADATA.replace("experimental=False", "experimental=True"))
        with patch.dict(os.environ, {"QGIS_TOKEN": "test-token"}):
            self.upload_qgis()
        self.token_upload.assert_called_once()

    def test_qgis_checks_the_downloaded_version(self):
        self.github.exists, self.github.draft = True, False
        self.write_plugin(METADATA.replace(VERSION, "26.8"))
        with patch.dict(os.environ, {"QGIS_TOKEN": "test-token"}), self.assertRaisesRegex(ValueError, "version"):
            self.upload_qgis()
        self.token_upload.assert_not_called()

    def test_qgis_requires_the_github_release_to_be_published(self):
        self.github.exists = True
        with patch.dict(os.environ, {"QGIS_TOKEN": "test-token"}), self.assertRaisesRegex(ValueError, "published"):
            self.upload_qgis()
        self.token_upload.assert_not_called()

    def test_partial_qgis_credentials_are_rejected(self):
        with patch.dict(os.environ, {"OSGEO_USERNAME": "test-user"}), self.assertRaisesRegex(ValueError, "together"):
            self.upload_qgis()
        self.token_upload.assert_not_called()
        self.password_upload.assert_not_called()

    def test_qgis_token_takes_precedence_over_legacy_credentials(self):
        self.github.exists, self.github.draft = True, False
        with patch.dict(os.environ, {"QGIS_TOKEN": "test-token", "OSGEO_USERNAME": "unused-user"}):
            self.upload_qgis()
        self.token_upload.assert_called_once()
        self.password_upload.assert_not_called()

    def test_qgis_publication_is_optional(self):
        self.upload_qgis()
        self.token_upload.assert_not_called()
        self.password_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
