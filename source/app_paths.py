"""Writable locations for the portable Windows app and installed Mac app."""
from pathlib import Path
import os
import sys
import tempfile


def project_root():
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            # ClickTrack.app/Contents/MacOS/ClickTrack; never write into the bundle.
            return Path(sys.executable).resolve().parents[3]
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


def data_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ClickTrack"
    return project_root() / "data"


def export_directory():
    if sys.platform == "darwin":
        return Path.home() / "Documents" / "ClickTrack"
    return project_root() / "outputs"


def configure_runtime_temp():
    directory = (data_directory() / "cache" / "temp" if sys.platform == "darwin"
                 else project_root() / "work" / "temp")
    directory.mkdir(parents=True, exist_ok=True)
    # These changes affect this app and its decoder subprocesses only.
    os.environ["TEMP"] = str(directory)
    os.environ["TMP"] = str(directory)
    os.environ["TMPDIR"] = str(directory)
    tempfile.tempdir = str(directory)
    return directory
