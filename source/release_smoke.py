"""Headless startup and dependency check for the actual distributable executable.

Run the packaged ClickTrack with ``--self-test --self-test-report report.json``.
All songs, preferences, and generated audio stay in a temporary directory. This
checks offline rendering, not the timing or sound of a physical output device.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

import numpy as np
import PySide6
import soundfile as sf
from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication

from audio_core import ClickSettings, _ffmpeg_path, analyze_audio, export_click_wav, load_audio
from playback import _fill_output, _make_state
from song_library import SongLibrary


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


@contextmanager
def _isolated_runtime(application_module, directory):
    """Keep the normal app's settings and library locations out of this check."""
    song_module = sys.modules[SongLibrary.__module__]
    old_data = application_module.data_directory
    old_song_data = song_module.data_directory
    old_devices = application_module.sd.query_devices
    old_apis = application_module.sd.query_hostapis
    old_temp = tempfile.tempdir
    old_environment = {name: os.environ.get(name) for name in ("TEMP", "TMP", "TMPDIR")}
    temporary_data = directory / "data"
    temporary_data.mkdir()
    application_module.data_directory = lambda: temporary_data
    song_module.data_directory = lambda: temporary_data
    # Load PortAudio but do not enumerate or open hardware on a CI runner.
    application_module.sd.query_devices = lambda: []
    application_module.sd.query_hostapis = lambda: []
    tempfile.tempdir = str(directory)
    for name in old_environment:
        os.environ[name] = str(directory)
    try:
        yield temporary_data
    finally:
        application_module.data_directory = old_data
        song_module.data_directory = old_song_data
        application_module.sd.query_devices = old_devices
        application_module.sd.query_hostapis = old_apis
        tempfile.tempdir = old_temp
        for name, value in old_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _is_bundled_decoder(path):
    if not getattr(sys, "frozen", False):
        return False
    roots = [Path(sys._MEIPASS).resolve()]
    if sys.platform == "darwin":
        # A macOS bundle may symlink binary/data files between its two folders.
        roots.append(Path(sys.executable).resolve().parent.parent)
    return any(path.resolve().is_relative_to(root) for root in roots)


def _run_checks(window, directory, report):
    checks = report["checks"]
    QApplication.processEvents()
    _require(window.isVisible() and not window.grab().isNull(), "The Qt window did not render.")
    checks["qt_window"] = True

    fixture = directory / "known-120-bpm.wav"
    settings = ClickSettings(bpm=120, accent=False, offset_seconds=0.3)
    export_click_wav(fixture, settings, 12)
    audio = load_audio(fixture)
    _require(audio.sample_rate == 44100 and audio.samples.shape == (12 * 44100, 2),
             "Generated WAV could not be imported at its expected length and sample rate.")
    _require(np.isfinite(audio.samples).all() and np.max(np.abs(audio.samples)) > 0,
             "Imported WAV was silent or contained invalid samples.")
    checks["wav_import"] = True

    analysis = analyze_audio(audio)
    report["detected_bpm"] = round(float(analysis.bpm), 3)
    report["detected_first_beat_seconds"] = round(float(analysis.first_beat_seconds), 4)
    _require(abs(analysis.bpm - 120) < 1 and abs(analysis.first_beat_seconds - 0.3) < 0.04,
             "Known pulse did not produce the expected tempo and first beat.")
    checks["tempo_analysis"] = True

    decoder = Path(_ffmpeg_path())
    report["decoder_bundled"] = _is_bundled_decoder(decoder)
    if report["frozen"]:
        _require(report["decoder_bundled"], "Frozen app used an external decoder instead of its bundled decoder.")
    decoded_path = directory / "decoder-output.wav"
    decoded = subprocess.run(
        [str(decoder), "-hide_banner", "-loglevel", "error", "-nostdin",
         "-y", "-protocol_whitelist", "file,pipe", "-i", str(fixture), "-map", "0:a:0",
         "-vn", "-ac", "1", "-ar", "11025", "-c:a", "pcm_f32le", "-f", "wav", str(decoded_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30, check=False)
    _require(decoded.returncode == 0 and decoded_path.is_file(),
             "Audio decoder did not decode the generated WAV.")
    decoded_samples, decoded_rate = sf.read(str(decoded_path), dtype="float32", always_2d=True)
    _require(decoded_rate == 11025 and decoded_samples.shape == (12 * 11025, 1),
             "Audio decoder did not produce the expected mono WAV length and sample rate.")
    _require(np.isfinite(decoded_samples).all() and np.max(np.abs(decoded_samples)) > 0,
             "Audio decoder produced invalid or silent audio.")
    checks["decoder"] = True

    first = window.library.add(fixture, title="120 BPM fixture", bpm=120,
                               offset_seconds=0.3, duration_seconds=12, analyzed=True)
    second = window.library.add(directory / "manual-preset.wav", title="90 BPM preset",
                                bpm=90, duration_seconds=12, beats_per_bar=3)
    window.library.reorder([second["id"], first["id"]])
    _require([item["id"] for item in SongLibrary(window.library.path).songs] == [second["id"], first["id"]],
             "Song order did not persist.")
    window.load_library_song(second["id"])
    _require(window.bpm.value() == 90 and window.meter.currentData() == 3,
             "Manual song preset did not load its click settings.")
    window.load_library_song(first["id"])
    _require(window.bpm.value() == 120 and abs(window.offset.value() - 0.3) < 1e-6,
             "Selected song did not restore its click tempo and first beat.")
    checks["library_presets"] = True

    window.transport.snap_checkbox.setChecked(True)
    snapped = window.transport.snap_position(2.41)
    _require(abs(snapped - 2.3) < 1e-6, "Scrubber did not snap to the beat grid.")
    window._seek_position(snapped)
    _require(abs(window.transport.position_seconds - 2.3) < 1e-6,
             "Selected scrubber position did not reach the song controls.")
    checks["beat_snapping"] = True

    window._loop_changed(True, 2.3, 4.3)
    _require(window.transport.loop_enabled and abs(window.transport.loop_start_seconds - 2.3) < 1e-6
             and abs(window.transport.loop_end_seconds - 4.3) < 1e-6,
             "Practice loop did not apply its brackets.")
    window.load_library_song(second["id"])
    window.load_library_song(first["id"])
    _require(window.transport.loop_enabled and abs(window.transport.loop_start_seconds - 2.3) < 1e-6
             and abs(window.transport.loop_end_seconds - 4.3) < 1e-6,
             "Practice loop was not restored when selecting its song again.")
    checks["practice_loop"] = True

    # Exercise the real playback mixer over multiple wraps without opening a device.
    state = _make_state(settings, audio, True, 12, start_seconds=2.3,
                        loop_start_seconds=2.3, loop_end_seconds=4.3)
    output = np.empty((round(6.25 * 44100), 2), dtype=np.float32)
    finished = _fill_output(state, output, len(output))
    loop_frames = 2 * 44100
    _require(not finished and np.isfinite(output).all() and np.max(np.abs(output)) > 0,
             "Offline practice loop did not produce continuous audio.")
    _require(np.array_equal(output[:loop_frames], output[loop_frames:2 * loop_frames])
             and np.array_equal(output[:loop_frames], output[2 * loop_frames:3 * loop_frames])
             and state.frame == round(2.55 * 44100),
             "Practice loop mixer did not wrap at the exact sample positions.")
    _require(not window.engine.running, "Self-test unexpectedly opened an audio device.")
    checks["offline_loop_render"] = True

    window.preferences.sync()
    _require(window.library.path.is_relative_to(directory)
             and Path(window.preferences.fileName()).is_relative_to(directory),
             "Self-test settings escaped the temporary directory.")
    checks["isolated_settings"] = True


def run_self_test(application_module, arguments):
    """Show the real window, execute deterministic checks, write JSON, then exit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True)
    parser.add_argument("--self-test-report", type=Path, required=True)
    options = parser.parse_args(arguments)
    report_path = options.self_test_report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "status": "failed",
        "platform": sys.platform,
        "architecture": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "qt_version": qVersion(),
        "pyside_version": PySide6.__version__,
        "audio_output_tested": False,
        "checks": {},
    }
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication(["ClickTrack-self-test"])
    application.setApplicationName(application_module.APP_NAME)
    application.setStyle("Fusion")
    application.setStyleSheet(application_module.STYLE)
    application.setQuitOnLastWindowClosed(False)
    try:
        with tempfile.TemporaryDirectory(prefix=".clicktrack-self-test-", dir=report_path.parent) as temporary:
            directory = Path(temporary)
            with _isolated_runtime(application_module, directory) as data:
                window = application_module.MainWindow(library_path=data / "library.json")
                window.show()

                def execute():
                    try:
                        _run_checks(window, directory, report)
                        report["status"] = "passed"
                    except Exception as exc:
                        report["error"] = f"{type(exc).__name__}: {exc}"
                    finally:
                        window.close()
                        application.quit()

                QTimer.singleShot(0, execute)
                application.exec()
                window.deleteLater()
                application.processEvents()
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
    temporary_report = report_path.with_name(report_path.name + ".tmp")
    temporary_report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary_report, report_path)
    return 0 if report["status"] == "passed" else 1
