"""Project-relative writable locations for the portable ClickTrack app."""
from pathlib import Path
import os
import sys
import tempfile


def project_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


def data_directory():
    return project_root() / "data"


def configure_runtime_temp():
    directory = project_root() / "work" / "temp"
    directory.mkdir(parents=True, exist_ok=True)
    # These changes affect this app and its decoder subprocesses only.
    os.environ["TEMP"] = str(directory)
    os.environ["TMP"] = str(directory)
    tempfile.tempdir = str(directory)
    return directory
