#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run with: uv run scripts/stage_plugin.py /tmp/ccd-plugin-stage

import argparse
import shutil
from pathlib import Path

FILES = (
    "__init__.py",
    "CCD_Plugin.py",
    "LICENSE",
    "Readme.md",
    "metadata.txt",
    "resources.py",
    "screenshot.webp",
)
DIRECTORIES = ("core", "gui", "icons", "ui", "utils")


def stage_plugin(source: Path, stage: Path) -> None:
    plugin = stage / "CCD_Plugin"
    if stage.exists() and any(stage.iterdir()):
        raise FileExistsError(f"Stage directory is not empty: {stage}")
    plugin.mkdir(parents=True)
    shutil.copy2(source / "pyproject.toml", stage / "pyproject.toml")
    for name in FILES:
        shutil.copy2(source / name, plugin / name)
    for name in DIRECTORIES:
        shutil.copytree(
            source / name,
            plugin / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.db", "*.sh"),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage CCD_Plugin sources for qgis-plugin-ci")
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    stage_plugin(Path.cwd(), arguments.destination.resolve())


if __name__ == "__main__":
    main()
