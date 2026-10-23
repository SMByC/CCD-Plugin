"""Validate release archives and publish the same files to GitHub and QGIS independently.

GitHub publication is retryable: replace draft assets, but preserve completed public assets.
QGIS publication only uploads the already-validated plugin ZIP, so a QGIS outage cannot leave
the GitHub release without its dependency archive or force another GitHub upload on retry.
"""

import argparse
import configparser
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path


def _plugin_metadata(archive: zipfile.ZipFile) -> configparser.ConfigParser:
    metadata = configparser.ConfigParser()
    try:
        metadata.read_string(archive.read("CCD_Plugin/metadata.txt").decode("utf-8"))
        dict(metadata["general"])  # exercise QGIS' interpolation for every field
    except (configparser.Error, KeyError, UnicodeDecodeError) as error:
        raise ValueError(f"Invalid plugin metadata: {error}") from error
    return metadata


def validate_archives(version: str, plugin: Path, extlibs: Path) -> None:
    """Check the actual artifacts before any remote release is created or updated."""
    with zipfile.ZipFile(plugin) as archive:
        names = set(archive.namelist())
        required = {f"CCD_Plugin/{name}" for name in ("__init__.py", "metadata.txt", "resources.py", "screenshot.webp")}
        if not required <= names:
            raise ValueError(f"Plugin archive is missing: {', '.join(sorted(required - names))}")
        excluded = re.compile(r"resources_rc|resources\.qrc|/tests/|__pycache__|\.py[co]$")
        if any(excluded.search(name) or not name.startswith("CCD_Plugin/") for name in names):
            raise ValueError("Plugin archive contains excluded files or an unexpected root directory")
        general = dict(_plugin_metadata(archive)["general"])
        if general.get("version") != version:
            raise ValueError(f"Plugin archive version does not match {version}")
        if general.get("qgisminimumversion") != "4.0" or {"qgismaximumversion", "supportsqt6"} & general.keys():
            raise ValueError("Plugin archive is not configured for QGIS 4")
        if b"PyQt5" in archive.read("CCD_Plugin/resources.py"):
            raise ValueError("Plugin archive contains Qt 5 resources")
        if archive.testzip() is not None:
            raise ValueError("Plugin archive failed its integrity check")

    with zipfile.ZipFile(extlibs) as archive:
        required = {"plotly/__init__.py", "plotly/package_data/plotly.min.js"}
        if not required <= set(archive.namelist()) or archive.testzip() is not None:
            raise ValueError("Dependency archive is incomplete or corrupt")


def _gh(*arguments: str) -> str:
    return subprocess.run(["gh", *arguments], check=True, stdout=subprocess.PIPE, text=True).stdout


def _release(repository: str, version: str) -> dict | None:
    # Listing releases also finds drafts, unlike the REST endpoint for a published tag.
    matches = _gh(
        "api",
        f"repos/{repository}/releases",
        "--paginate",
        "--jq",
        f".[] | select(.tag_name == {json.dumps(version)}) | @json",
    ).splitlines()
    return json.loads(matches[0]) if matches else None


def prepare_metadata(repository: str, version: str, path: Path) -> None:
    """Carry a GitHub prerelease flag into staged metadata before the packaging commit.

    Explicit experimental=True in the source is also preserved for ordinary GitHub releases,
    matching qgis-plugin-ci's former behavior. The working tree's source metadata is not edited.
    """
    release = _release(repository, version)
    metadata = configparser.ConfigParser()
    metadata.optionxform = str  # qgis-plugin-ci reads case-sensitive keys such as qgisMinimumVersion
    metadata.read_string(path.read_text(encoding="utf-8"))
    if metadata.get("general", "version") != version:
        raise ValueError(f"Staged plugin version does not match {version}")
    if release is not None and release["prerelease"]:
        metadata.set("general", "experimental", "True")
        with path.open("w", encoding="utf-8") as stream:
            metadata.write(stream, space_around_delimiters=False)


def _check_release_metadata(version: str, plugin: Path, release: dict) -> None:
    with zipfile.ZipFile(plugin) as archive:
        metadata = _plugin_metadata(archive)
        if metadata.get("general", "version") != version:
            raise ValueError(f"Plugin archive version does not match {version}")
        if release["prerelease"] and not metadata.getboolean("general", "experimental", fallback=False):
            raise ValueError("A GitHub prerelease requires experimental=True in the plugin archive")


def publish_github(repository: str, version: str, plugin: Path, extlibs: Path, notes: Path) -> None:
    validate_archives(version, plugin, extlibs)
    if not notes.is_file():
        raise FileNotFoundError(notes)
    release = _release(repository, version)
    if release is None:
        _gh(
            "release",
            "create",
            version,
            "--repo",
            repository,
            "--draft",
            "--verify-tag",
            "--title",
            version,
            "--notes-file",
            str(notes),
        )
        release = _release(repository, version)
        if release is None:
            raise RuntimeError("The newly created GitHub draft could not be found")

    # Recheck after packaging: the owner may have changed the release's prerelease flag.
    _check_release_metadata(version, plugin, release)
    assets = {asset["name"]: asset for asset in release["assets"]}
    for archive in (extlibs, plugin):
        asset = assets.get(archive.name)
        if not release["draft"] and asset is not None:
            if asset["state"] == "uploaded" and asset["size"] > 0:
                # A rebuild can resolve newer dependencies. The already-published bytes are the
                # release; never delete them just because someone reran the workflow.
                continue
            if asset["state"] == "starter" and asset["size"] == 0:
                # GitHub documents these empty placeholders after failed uploads as safe to delete.
                _gh("api", f"repos/{repository}/releases/assets/{asset['id']}", "--method", "DELETE")
            else:
                raise ValueError(f"Published asset {archive.name} is incomplete; refusing to replace it automatically")
        options = ["--clobber"] if release["draft"] else []
        _gh("release", "upload", version, str(archive), "--repo", repository, *options)
    if release["draft"]:
        _gh("api", f"repos/{repository}/releases/{release['id']}", "--method", "PATCH", "-F", "draft=false")


def publish_qgis(plugin: Path, *, repository: str, version: str) -> None:
    """Upload a built plugin archive using the optional credentials in the job environment."""
    if not plugin.is_file():
        raise FileNotFoundError(plugin)
    token = os.environ.get("QGIS_TOKEN")
    username, password = os.environ.get("OSGEO_USERNAME"), os.environ.get("OSGEO_PASSWORD")
    if not token and bool(username) != bool(password):
        raise ValueError("OSGEO_USERNAME and OSGEO_PASSWORD must be configured together")
    if not token and not username:
        print("No QGIS publication credentials configured; GitHub release is complete.")
        return

    release = _release(repository, version)
    if release is None or release["draft"]:
        raise ValueError("QGIS publication requires a published GitHub release")
    # Validate the downloaded bytes, not a local rebuild that may differ from preserved public
    # assets. A prerelease flag changed after packaging must never silently publish as stable.
    _check_release_metadata(version, plugin, release)

    # Use the upload-only API of the pinned qgis-plugin-ci: never rebuild or republish to GitHub.
    from qgispluginci.release import upload_plugin_to_osgeo_with_token, upload_plugin_to_osgeo_xml_rpc

    if token:
        upload_plugin_to_osgeo_with_token(archive=str(plugin), package_name="CCD_Plugin", token=token)
    else:
        upload_plugin_to_osgeo_xml_rpc(username=username, password=password, archive=str(plugin))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("version")
    prepare.add_argument("metadata", type=Path)
    prepare.add_argument("--repository", required=True)
    for name in ("check", "github"):
        command = commands.add_parser(name)
        command.add_argument("version")
        command.add_argument("plugin", type=Path)
        command.add_argument("extlibs", type=Path)
        if name == "github":
            command.add_argument("--repository", required=True)
            command.add_argument("--notes", type=Path, required=True)
    qgis = commands.add_parser("qgis")
    qgis.add_argument("plugin", type=Path)
    qgis.add_argument("--repository", required=True)
    qgis.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare_metadata(args.repository, args.version, args.metadata)
    elif args.command == "check":
        validate_archives(args.version, args.plugin, args.extlibs)
    elif args.command == "github":
        publish_github(args.repository, args.version, args.plugin, args.extlibs, args.notes)
    else:
        publish_qgis(args.plugin, repository=args.repository, version=args.version)


if __name__ == "__main__":
    main()
