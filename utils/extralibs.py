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

import configparser
import os
import shutil
import ssl
import tempfile
import urllib.request
import zipfile

from qgis.core import Qgis, QgsApplication, QgsMessageLog
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


def _get_plugin_version() -> str:
    """Read the plugin version from ``metadata.txt``"""
    metadata_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "metadata.txt",
    )
    config = configparser.ConfigParser()
    config.read(metadata_path, encoding="utf-8")
    return config["general"]["version"]


EXTLIBS_DOWNLOAD_URL = f"https://github.com/SMByC/CCD-Plugin/releases/download/{_get_plugin_version()}/extlibs.zip"


def _log(msg: str, level: str = "Info") -> None:
    """Write *msg* to the QGIS message log (and stdout as fallback)"""
    try:
        qgis_level = getattr(getattr(Qgis, "MessageLevel", Qgis), level)
        QgsMessageLog.logMessage(msg, tag="CCD-Plugin", level=qgis_level)
    except Exception:
        print(f"[CCD-Plugin] {msg}")


class DownloadAndUnzip(QDialog):
    """Modal dialog that downloads a ZIP from *url* and extracts it to *output_path*"""

    def __init__(self, url: str, output_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CCD-Plugin Installation")
        self.setModal(True)
        self.setMinimumWidth(420)
        # Keep dialog on top of the QGIS main window
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        self.url = url
        self.output_path = output_path
        self._zip_fd: int | None = None
        self._zip_path: str | None = None
        self._cancelled = False

        self.progress_label = QLabel("Downloading additional libraries...", self)
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        progress_layout = QVBoxLayout()
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)

        cancel_button = QPushButton("Cancel", self)
        cancel_button.clicked.connect(self._on_cancel)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)

        main_layout = QVBoxLayout(self)
        main_layout.addLayout(progress_layout)
        main_layout.addLayout(button_layout)
        # Size the dialog to fit its content
        self.adjustSize()

        self.show()
        QApplication.processEvents()

        self._zip_fd, self._zip_path = tempfile.mkstemp(suffix=".zip")

        downloaded_ok = self.download_file()
        extracted_ok = (not self._cancelled) and downloaded_ok and self.extract_zip()

        if extracted_ok:
            self.progress_label.setText("Done!")
            self.progress_bar.setValue(100)
        elif not self._cancelled:
            _log("Failed to download/extract extra libraries.", level="Critical")
            QMessageBox.critical(
                None,
                "CCD-Plugin: Error installing libs",
                (
                    "Error downloading and extracting additional Python packages"
                    " required for CCD-Plugin.\n\n"
                    "Read the install instructions here:\n"
                    "https://github.com/SMByC/CCD-Plugin#installation"
                ),
                QMessageBox.StandardButton.Ok,
            )

        self._cleanup()

    def _on_cancel(self) -> None:
        self._cancelled = True
        self._cleanup()

    def _cleanup(self) -> None:
        """Release the temporary ZIP file and close the dialog."""
        if self._zip_fd is not None:
            try:
                os.close(self._zip_fd)
            except OSError:
                pass
            self._zip_fd = None

        if self._zip_path and os.path.exists(self._zip_path):
            try:
                os.remove(self._zip_path)
            except OSError:
                pass
            self._zip_path = None

        try:
            self.deleteLater()
            self.accept()
        except RuntimeError:
            pass

    def download_file(self) -> bool:
        """Download ``self.url`` into the temporary ZIP file.

        Returns ``True`` on success, ``False`` on error or cancellation.
        """
        if self._zip_path is None:
            return False
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "CCD-Plugin"})
            with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as response:  # nosec B310
                raw_length = response.getheader("Content-Length")
                total_length: int | None = int(raw_length) if raw_length else None
                # Indeterminate progress when length is unknown
                self.progress_bar.setRange(0, 100 if total_length else 0)

                with open(self._zip_path, "wb") as fh:
                    downloaded = 0
                    while not self._cancelled:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total_length:
                            self.progress_bar.setValue(int(downloaded * 100 / total_length))
                        QApplication.processEvents()

            self.progress_bar.setRange(0, 100)
            return not self._cancelled
        except Exception as exc:
            _log(f"Download error: {exc}", level="Critical")
            return False

    def extract_zip(self) -> bool:
        """Extract the downloaded ZIP to ``self.output_path``.

        Entries whose resolved paths escape *output_path* (zip-slip attack)
        are rejected before any file is written.
        """
        if self._zip_path is None:
            return False
        self.progress_label.setText("Extracting libraries...")
        QApplication.processEvents()
        try:
            real_output = os.path.realpath(self.output_path)
            with zipfile.ZipFile(self._zip_path, "r") as zf:
                # Validate every entry before writing anything
                for member in zf.infolist():
                    member_dest = os.path.realpath(os.path.join(real_output, member.filename))
                    if not (member_dest == real_output or member_dest.startswith(real_output + os.sep)):
                        raise ValueError(f"Zip-slip rejected for entry: {member.filename!r}")
                zf.extractall(real_output)
            return True
        except Exception as exc:
            _log(f"Extraction error: {exc}", level="Critical")
            return False


def get_extlibs_install_path() -> str:
    """Return the ``extlibs`` directory inside this plugin."""
    return os.path.join(
        QgsApplication.qgisSettingsDirPath(),
        "python",
        "plugins",
        "CCD_Plugin",
        "extlibs",
    )


def install() -> None:
    """Download and install the extra Python libraries required by CCD-Plugin."""
    extlibs_dir = get_extlibs_install_path()
    if os.path.isdir(extlibs_dir):
        _log(f"Removing existing extlibs at: {extlibs_dir}")
        shutil.rmtree(extlibs_dir, ignore_errors=True)
    os.makedirs(extlibs_dir, exist_ok=True)
    _log(f"Installing extra libs to: {extlibs_dir}")
    DownloadAndUnzip(EXTLIBS_DOWNLOAD_URL, extlibs_dir)
