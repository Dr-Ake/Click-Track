# -*- mode: python ; coding: utf-8 -*-
"""Build a native macOS application; run through Build-Mac.sh on a Mac."""
import os
from pathlib import Path
import platform
import re
import shutil
import sys

if sys.platform != "darwin":
    raise RuntimeError("The Mac application must be built on macOS.")

source = Path(SPECPATH).resolve()
root = source.parent
architecture = os.environ.get("CLICKTRACK_ARCH", platform.machine())
if architecture not in ("arm64", "x86_64") or architecture != platform.machine():
    raise RuntimeError("Use a native arm64 or x86_64 Python for the matching Mac release.")

version = os.environ.get("CLICKTRACK_VERSION", "1.0.0").removeprefix("v")
if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
    raise ValueError("The Mac release version must contain three numbers, such as 1.0.0.")
minimum_system = os.environ.get("CLICKTRACK_MAC_MIN_VERSION", "14.0")
if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", minimum_system):
    raise ValueError("CLICKTRACK_MAC_MIN_VERSION must be a macOS version such as 14.0.")

decoder_name = os.environ.get("CLICKTRACK_FFMPEG") or shutil.which("ffmpeg")
if not decoder_name or not Path(decoder_name).is_file():
    raise RuntimeError("Provide a native FFmpeg executable with CLICKTRACK_FFMPEG.")
decoder = Path(decoder_name).resolve()
if decoder.name != "ffmpeg":
    raise RuntimeError("The Mac audio decoder must be named ffmpeg.")
icon = Path(os.environ.get("CLICKTRACK_MAC_ICON", root / "work/mac-icon/ClickTrack.icns"))
if not icon.is_file():
    raise RuntimeError("The Mac icon is missing; build with source/Build-Mac.sh.")

a = Analysis(
    [str(source / "app.py")], pathex=[str(source)],
    binaries=[(str(decoder), ".")], datas=[(str(root / "assets"), "assets")],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False, optimize=0,
)
# The sounddevice hook collects every libportaudio* file supplied by its wheel.
# Keep the native CoreAudio dylib; Windows/ASIO variants are never used here.
def native_portaudio_only(entry):
    name = entry[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return "portaudio" not in name or (name.endswith(".dylib") and "asio" not in name)

a.binaries = [entry for entry in a.binaries if native_portaudio_only(entry)]
a.datas = [entry for entry in a.datas if native_portaudio_only(entry)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="ClickTrack",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=architecture, codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ClickTrack")
app = BUNDLE(
    coll, name="ClickTrack.app", icon=str(icon),
    bundle_identifier="com.drake.clicktrack", version=version,
    info_plist={
        "CFBundleName": "ClickTrack",
        "CFBundleDisplayName": "ClickTrack",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": minimum_system,
    },
)
