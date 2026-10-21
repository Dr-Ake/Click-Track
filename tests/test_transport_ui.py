"""Native integration checks for song seeking and practice loops."""
from pathlib import Path
import sys
import time

import numpy as np
import pytest
import soundfile as sf

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'source'))

from PySide6.QtCore import QSettings, Qt, QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import app as appmod
from song_library import SongLibrary


def pump(seconds=.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)


def wait_jobs(window, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if not window.jobs and not window._import_queue:
            pump(.05)
            return
        time.sleep(.01)
    pytest.fail('Audio tasks did not finish in time')


def fixture_song(path, seconds=12):
    rate = 44100
    samples = np.zeros((seconds * rate, 2), dtype=np.float32)
    t = np.arange(round(.065 * rate)) / rate
    pulse = (np.sin(2 * np.pi * 110 * t) * np.exp(-48 * t) * .4).astype(np.float32)
    for beat in np.arange(.3, seconds - .1, .5):
        start = round(beat * rate)
        samples[start:start + len(pulse)] += pulse[:, None]
    sf.write(path, samples, rate)
    return path


class FakeEngine:
    def __init__(self):
        self.running = False
        self.position_seconds = 0.0
        self.error = None
        self.starts = []
        self.stops = 0
        self.latency_seconds = 0.0

    def start(self, settings, **kwargs):
        self.starts.append((settings, dict(kwargs)))
        self.position_seconds = kwargs.get('start_seconds', 0.0)
        self.running = True

    def stop(self):
        self.running = False
        self.stops += 1


@pytest.fixture(scope='session')
def qtapp():
    q = QApplication.instance() or QApplication([])
    q.setStyle('Fusion')
    q.setStyleSheet(appmod.STYLE)
    q.setQuitOnLastWindowClosed(False)
    return q


@pytest.fixture
def make_window(qtapp, tmp_path, monkeypatch):
    windows = []
    monkeypatch.setattr(appmod, 'QSettings', lambda *args: QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))

    def create(entries=None, fake=True, library_path=None):
        path = Path(library_path) if library_path else tmp_path / f'library-{len(windows)}.json'
        store = SongLibrary(path)
        songs = store.songs
        source_entries = entries if entries is not None else [] if songs else [{}]
        for index, fields in enumerate(source_entries):
            fields = dict(fields)
            source = fields.pop('path', fixture_song(tmp_path / f'song-{len(windows)}-{index}.wav'))
            args = dict(bpm=120, offset_seconds=.3, duration_seconds=12,
                        analyzed=True, include_song=False, volume=.04)
            args.update(fields)
            songs.append(store.add(str(source), **args))
        window = appmod.MainWindow(library_path=path)
        window.test_errors = []
        monkeypatch.setattr(window, 'show_error', lambda title, text: window.test_errors.append((title, text)))
        if fake:
            window.engine = FakeEngine()
        windows.append(window)
        window.show()
        pump(.08)
        if songs:
            window.load_library_song(songs[0]['id'])
            pump(.03)
        return window, songs

    yield create
    for window in windows:
        window.stop()
        wait_jobs(window)
        window.close()
        pump(.04)


def seek(window, seconds):
    window.transport.set_position(seconds)
    window.transport.seekRequested.emit(seconds)
    pump(.03)


def loop(window, enabled, start, end):
    window.transport.set_loop(enabled, start, end)
    window.transport.loopChanged.emit(enabled, start, end)
    pump(.03)


def test_song_seek_uses_absolute_grid_even_without_song_audio(make_window):
    w, songs = make_window([dict(bpm=120, offset_seconds=.3, beats_per_bar=3)])
    assert w.audio is None
    seek(w, 3.3)
    w.toggle_play()
    settings, kwargs = w.engine.starts[-1]
    assert settings.offset_seconds == .3
    assert settings.beats_per_bar == 3
    assert kwargs['start_seconds'] == pytest.approx(3.3)
    assert kwargs['duration_seconds'] == 12
    assert kwargs['include_song'] is False
    assert w.transport.position_seconds == pytest.approx(3.3)
    assert not w.test_errors


def test_pause_preserves_audible_position_and_resume(make_window):
    w, _ = make_window()
    seek(w, 2.3)
    w.toggle_play()
    w.engine.position_seconds = 4.217
    w.toggle_play()
    assert not w.engine.running
    assert w.transport.position_seconds == pytest.approx(4.217, abs=.001)
    w.toggle_play()
    assert w.engine.starts[-1][1]['start_seconds'] == pytest.approx(4.217, abs=.001)
    assert w.engine.starts[-1][0].offset_seconds == .3


def test_seek_during_playback_restarts_at_requested_song_time(make_window):
    w, _ = make_window()
    w.toggle_play()
    assert w.engine.running
    seek(w, 6.3)
    assert w.engine.running
    assert len(w.engine.starts) == 2
    assert w.engine.starts[-1][1]['start_seconds'] == pytest.approx(6.3)
    assert w.engine.starts[-1][0].offset_seconds == .3


def test_loop_bounds_reach_engine_and_follow_playing_edits(make_window):
    w, _ = make_window()
    loop(w, True, 2.3, 5.3)
    seek(w, 3.3)
    w.toggle_play()
    kwargs = w.engine.starts[-1][1]
    assert kwargs['loop_start_seconds'] == pytest.approx(2.3)
    assert kwargs['loop_end_seconds'] == pytest.approx(5.3)
    assert kwargs['start_seconds'] == pytest.approx(3.3)
    loop(w, True, 3.3, 4.3)
    assert w.engine.running
    kwargs = w.engine.starts[-1][1]
    assert kwargs['loop_start_seconds'] == pytest.approx(3.3)
    assert kwargs['loop_end_seconds'] == pytest.approx(4.3)
    loop(w, False, 3.3, 4.3)
    assert w.engine.running
    kwargs = w.engine.starts[-1][1]
    assert kwargs.get('loop_start_seconds') is None
    assert kwargs.get('loop_end_seconds') is None


def test_loop_bookmarks_are_per_song_and_survive_restart(make_window):
    w, songs = make_window([dict(bpm=120), dict(bpm=150, offset_seconds=.2)])
    path = w.library.path
    loop(w, True, 2.3, 5.3)
    w.load_library_song(songs[1]['id'])
    assert w.transport.position_seconds == 0
    assert not w.transport.loop_enabled
    loop(w, True, 1.4, 3.4)
    w.load_library_song(songs[0]['id'])
    assert w.transport.position_seconds == pytest.approx(2.3)
    assert w.transport.loop_enabled
    assert w.transport.loop_start_seconds == pytest.approx(2.3)
    assert w.transport.loop_end_seconds == pytest.approx(5.3)
    w.close()
    pump(.05)
    reopened, _ = make_window(library_path=path)
    assert reopened.transport.loop_enabled
    assert reopened.transport.loop_start_seconds == pytest.approx(2.3)
    assert reopened.transport.loop_end_seconds == pytest.approx(5.3)


def test_lazy_audio_start_uses_latest_seek_and_loop(make_window, monkeypatch):
    real_load = appmod.load_audio
    def slow_load(*args, **kwargs):
        time.sleep(.2)
        return real_load(*args, **kwargs)
    monkeypatch.setattr(appmod, 'load_audio', slow_load)
    w, _ = make_window([dict(include_song=True)])
    seek(w, 1.3)
    w.toggle_play()
    assert w._awaiting_song_start is not None
    seek(w, 4.3)
    loop(w, True, 3.3, 6.3)
    wait_jobs(w)
    assert len(w.engine.starts) == 1
    settings, kwargs = w.engine.starts[0]
    assert kwargs['include_song'] is True
    assert kwargs['audio'] is w.audio
    assert kwargs['start_seconds'] == pytest.approx(4.3)
    assert kwargs['loop_start_seconds'] == pytest.approx(3.3)
    assert kwargs['loop_end_seconds'] == pytest.approx(6.3)
    assert settings.offset_seconds == .3


def test_song_switch_cancels_old_start_and_resets_new_timeline(make_window, monkeypatch):
    real_load = appmod.load_audio
    def slow_load(*args, **kwargs):
        time.sleep(.2)
        return real_load(*args, **kwargs)
    monkeypatch.setattr(appmod, 'load_audio', slow_load)
    w, songs = make_window([dict(include_song=True), dict(bpm=150)])
    seek(w, 5.3)
    loop(w, True, 4.3, 7.3)
    w.toggle_play()
    w.load_library_song(songs[1]['id'])
    wait_jobs(w)
    assert w.active_song_id == songs[1]['id']
    assert w.transport.position_seconds == 0
    assert not w.transport.loop_enabled
    assert not w.engine.running
    assert not w.engine.starts


def test_missing_song_retains_seek_and_loop_click_practice(make_window, tmp_path):
    w, _ = make_window([dict(path=tmp_path/'Moved.wav', duration_seconds=90,
                           bpm=100, offset_seconds=.6)])
    seek(w, 24.6)
    loop(w, True, 24.6, 27.0)
    w.toggle_play()
    settings, kwargs = w.engine.starts[-1]
    assert kwargs['include_song'] is False
    assert kwargs['start_seconds'] == pytest.approx(24.6)
    assert kwargs['duration_seconds'] == 90
    assert kwargs['loop_start_seconds'] == pytest.approx(24.6)
    assert kwargs['loop_end_seconds'] == pytest.approx(27.0)
    assert settings.offset_seconds == .6


def test_native_song_scrubber_layout_and_precise_time(make_window):
    w, _ = make_window()
    w.transport.snap_checkbox.setChecked(False)
    w.transport.position_spin.setValue(4.123)
    w.transport.position_spin.editingFinished.emit()
    pump(.06)
    w.toggle_play()
    assert w.engine.starts[-1][1]['start_seconds'] == pytest.approx(4.123, abs=.001)
    w.stop()
    out = BASE/'work/qa'
    out.mkdir(exist_ok=True)
    w.grab().save(str(out/'transport_normal.png'))
    assert w.transport.isVisible()
    assert w.centralWidget().horizontalScrollBar().maximum() == 0
    w.resize(w.minimumWidth(), w.minimumHeight())
    pump(.08)
    w.grab().save(str(out/'transport_minimum.png'))
    assert w.centralWidget().horizontalScrollBar().maximum() == 0


def test_actual_timeline_drag_snaps_and_restarts_only_on_release(make_window):
    w, _ = make_window()
    timeline = w.transport.timeline
    w.toggle_play()
    assert w.transport.snap_checkbox.isChecked()
    starts = len(w.engine.starts)
    press = QPoint(round(timeline.time_to_x(1.05)), 25)
    release = QPoint(round(timeline.time_to_x(3.08)), 25)
    QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=press)
    QTest.mouseMove(timeline, release)
    pump(.08)
    assert timeline.dragging
    assert len(w.engine.starts) == starts
    assert w.transport.position_seconds == pytest.approx(3.3)
    QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=release)
    pump(.04)
    assert not timeline.dragging
    assert len(w.engine.starts) == starts + 1
    assert w.engine.starts[-1][1]['start_seconds'] == pytest.approx(3.3)


def test_actual_brackets_drag_to_beats_and_enable_practice_loop(make_window):
    w, _ = make_window()
    timeline = w.transport.timeline
    def drag(old, new):
        QTest.mousePress(timeline, Qt.MouseButton.LeftButton,
                         pos=QPoint(round(timeline.time_to_x(old)), 47))
        QTest.mouseMove(timeline, QPoint(round(timeline.time_to_x(new)), 47))
        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton,
                           pos=QPoint(round(timeline.time_to_x(new)), 47))
        pump(.04)
    drag(0, 2.22)
    assert w.transport.loop_start_seconds == pytest.approx(2.3)
    drag(12, 6.22)
    assert w.transport.loop_end_seconds == pytest.approx(6.3)
    assert w.transport.loop_enabled
    w.toggle_play()
    kwargs = w.engine.starts[-1][1]
    assert kwargs['start_seconds'] == pytest.approx(2.3)
    assert kwargs['loop_start_seconds'] == pytest.approx(2.3)
    assert kwargs['loop_end_seconds'] == pytest.approx(6.3)


def test_real_audio_song_seek_loop_runs_multiple_cycles(require_audio_output, make_window):
    w, _ = make_window([dict(include_song=True, volume=.04)], fake=False)
    loop(w, True, 2.3, 2.8)
    seek(w, 2.3)
    w.toggle_play()
    wait_jobs(w)
    pump(1.65)
    assert w.engine.running
    assert w.engine.error is None
    assert 2.3 <= w.engine.position_seconds < 2.8
    w.stop()
    assert not w.engine.running
