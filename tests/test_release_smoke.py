"""Exercise the executable's CI entry point as a separate process."""
import json
import os
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]


def run_smoke(tmp_path, fail_decoder=False):
    report = tmp_path / "smoke.json"
    protected_library = tmp_path / "user-library.json"
    protected_library.write_text("Do not use the user's library in self-test.\n", encoding="utf-8")
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", CLICKTRACK_LIBRARY_PATH=str(protected_library))
    if fail_decoder:
        # The failure must be visible in the report and process exit status.
        command = [sys.executable, "-c",
                   "import app, release_smoke; "
                   "release_smoke._ffmpeg_path = lambda: 'missing-self-test-decoder'; "
                   "raise SystemExit(app.main())"]
    else:
        command = [sys.executable, str(BASE / "source" / "app.py")]
    result = subprocess.run(command + ["--self-test", "--self-test-report", str(report)],
                            cwd=BASE / "source", env=environment,
                            capture_output=True, text=True, timeout=60)
    assert report.is_file(), result.stderr
    assert protected_library.read_text(encoding="utf-8") == "Do not use the user's library in self-test.\n"
    assert not list(tmp_path.glob(".clicktrack-self-test-*"))
    return result, json.loads(report.read_text(encoding="utf-8"))


def test_release_self_test_starts_window_without_audio_device(tmp_path):
    result, report = run_smoke(tmp_path)
    assert result.returncode == 0, report
    assert report["status"] == "passed"
    assert report["frozen"] is False
    assert report["audio_output_tested"] is False
    assert report["qt_version"]
    assert abs(report["detected_bpm"] - 120) < 1
    assert report["checks"] == dict.fromkeys([
        "qt_window", "wav_import", "tempo_analysis", "decoder", "library_presets",
        "beat_snapping", "practice_loop", "offline_loop_render", "isolated_settings"], True)


def test_release_self_test_reports_missing_decoder_as_failure(tmp_path):
    result, report = run_smoke(tmp_path, fail_decoder=True)
    assert result.returncode == 1
    assert report["status"] == "failed"
    assert "FileNotFoundError" in report["error"]
    assert "decoder" not in report["checks"]
