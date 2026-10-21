"""Mac bundle contracts, checked without pretending to run a Mac on Windows."""
from pathlib import Path
import platform
import runpy
import sys
from types import SimpleNamespace

import pytest


SPEC = Path(__file__).resolve().parents[1] / "source/ClickTrack-mac.spec"


@pytest.fixture
def build_spec(tmp_path, monkeypatch):
    source = tmp_path / "project/source"
    source.mkdir(parents=True)
    (source / "app.py").write_text("# fixture app", encoding="utf-8")
    (source.parent / "assets").mkdir()
    decoder = tmp_path / "native decoder/ffmpeg"
    decoder.parent.mkdir()
    decoder.write_bytes(b"native executable placeholder")
    icon = tmp_path / "ClickTrack.icns"
    icon.write_bytes(b"icon placeholder")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setenv("CLICKTRACK_FFMPEG", str(decoder))
    monkeypatch.setenv("CLICKTRACK_MAC_ICON", str(icon))
    for key in ("CLICKTRACK_VERSION", "CLICKTRACK_ARCH", "CLICKTRACK_MAC_MIN_VERSION"):
        monkeypatch.delenv(key, raising=False)

    def execute(binary_entries=(), data_entries=()):
        calls = {}

        def analysis(*args, **kwargs):
            calls["analysis"] = (args, kwargs)
            return SimpleNamespace(pure=[], scripts=[], binaries=list(binary_entries), datas=list(data_entries))

        def record(name):
            def factory(*args, **kwargs):
                calls[name] = (args, kwargs)
                return SimpleNamespace()
            return factory

        runpy.run_path(str(SPEC), init_globals={
            "SPECPATH": str(source), "Analysis": analysis,
            "PYZ": record("pyz"), "EXE": record("exe"),
            "COLLECT": record("collect"), "BUNDLE": record("bundle"),
        })
        return calls

    return execute, source, decoder, icon


@pytest.mark.parametrize("architecture", ["arm64", "x86_64"])
def test_native_mac_bundle_has_runtime_decoder_and_assets_only(build_spec, monkeypatch, architecture):
    execute, source, decoder, icon = build_spec
    monkeypatch.setattr(platform, "machine", lambda: architecture)
    monkeypatch.setenv("CLICKTRACK_ARCH", architecture)
    monkeypatch.setenv("CLICKTRACK_VERSION", "v1.2.3")
    calls = execute()
    args, analysis = calls["analysis"]
    assert args == ([str(source / "app.py")],)
    assert analysis["binaries"] == [(str(decoder.resolve()), ".")]
    assert analysis["datas"] == [(str(source.parent / "assets"), "assets")]
    exe = calls["exe"][1]
    assert exe["target_arch"] == architecture
    assert exe["console"] is False
    assert exe["argv_emulation"] is False
    assert exe["codesign_identity"] is None
    bundle = calls["bundle"][1]
    assert bundle["name"] == "ClickTrack.app"
    assert bundle["icon"] == str(icon)
    assert bundle["bundle_identifier"] == "com.drake.clicktrack"
    assert bundle["version"] == "1.2.3"
    assert bundle["info_plist"]["CFBundleShortVersionString"] == "1.2.3"
    assert bundle["info_plist"]["CFBundleVersion"] == "1.2.3"
    assert bundle["info_plist"]["LSMinimumSystemVersion"] == "15.0"
    assert bundle["info_plist"]["NSHighResolutionCapable"] is True
    assert "NSMicrophoneUsageDescription" not in bundle["info_plist"]


def test_mac_spec_refuses_cross_platform_builds(build_spec, monkeypatch):
    execute, *_ = build_spec
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="must be built on macOS"):
        execute()


@pytest.mark.parametrize("architecture", ["x86_64", "universal2", "../arm64"])
def test_mac_spec_refuses_mismatched_or_unsupported_architecture(build_spec, monkeypatch, architecture):
    execute, *_ = build_spec
    monkeypatch.setenv("CLICKTRACK_ARCH", architecture)
    with pytest.raises(RuntimeError, match="native arm64 or x86_64"):
        execute()


@pytest.mark.parametrize("version", ["../1.2.3", "1.2", "01.2.3", "1.2.3\n", "1.2.3/../../"])
def test_mac_spec_rejects_unsafe_versions(build_spec, monkeypatch, version):
    execute, *_ = build_spec
    monkeypatch.setenv("CLICKTRACK_VERSION", version)
    with pytest.raises(ValueError, match="three numbers"):
        execute()


def test_mac_spec_rejects_missing_or_windows_decoder(build_spec, monkeypatch, tmp_path):
    execute, *_ = build_spec
    monkeypatch.setenv("CLICKTRACK_FFMPEG", str(tmp_path / "missing-ffmpeg"))
    with pytest.raises(RuntimeError, match="Provide a native FFmpeg"):
        execute()
    decoder = tmp_path / "ffmpeg.exe"
    decoder.write_bytes(b"Windows executable")
    monkeypatch.setenv("CLICKTRACK_FFMPEG", str(decoder))
    with pytest.raises(RuntimeError, match="must be named ffmpeg"):
        execute()


def test_mac_spec_honors_explicit_minimum_and_requires_icon(build_spec, monkeypatch):
    execute, _, _, icon = build_spec
    monkeypatch.setenv("CLICKTRACK_MAC_MIN_VERSION", "16.0")
    assert execute()["bundle"][1]["info_plist"]["LSMinimumSystemVersion"] == "16.0"
    monkeypatch.setenv("CLICKTRACK_MAC_MIN_VERSION", "14/../../")
    with pytest.raises(ValueError, match="macOS version"):
        execute()
    monkeypatch.setenv("CLICKTRACK_MAC_MIN_VERSION", "15.0")
    icon.unlink()
    with pytest.raises(RuntimeError, match="Mac icon is missing"):
        execute()


def test_mac_spec_keeps_native_portaudio_and_omits_unused_platform_variants(build_spec):
    execute, *_ = build_spec
    native = ("_sounddevice_data/portaudio-binaries/libportaudio.dylib", "/wheel/libportaudio.dylib", "BINARY")
    system_native = ("libportaudio.2.dylib", "/usr/local/lib/libportaudio.2.dylib", "BINARY")
    other = ("QtCore.framework/QtCore", "/wheel/QtCore", "BINARY")
    readme = ("_sounddevice_data/portaudio-binaries/README.md", "/wheel/README.md", "DATA")
    unwanted = [
        ("_sounddevice_data/portaudio-binaries/" + name, "/wheel/" + name, "BINARY")
        for name in ("libportaudio32bit.dll", "libportaudio64bit.dll", "libportaudio64bit-asio.dll",
                     "libportaudio-asio.dylib", "libportaudio.so.2")
    ]
    calls = execute([native, system_native, other, *unwanted], [readme, *unwanted])
    collected_args = calls["collect"][0]
    assert collected_args[1] == [native, system_native, other]
    assert collected_args[2] == [readme]


def test_mac_requirements_change_only_qt_and_use_same_test_runner():
    source = SPEC.parent
    def pins(path):
        return dict(line.strip().split("==", 1) for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith(("#", "-r")))
    common = pins(source / "requirements.txt")
    mac = pins(source / "requirements-mac.txt")
    assert mac.pop("PySide6-Essentials") == "6.11.2"
    common.pop("PySide6-Essentials")
    assert mac == common
    assert pins(source / "requirements-mac-dev.txt") == pins(source / "requirements-dev.txt")
    assert "-r requirements-mac.txt" in (source / "requirements-mac-dev.txt").read_text(encoding="utf-8")
