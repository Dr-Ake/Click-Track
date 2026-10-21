"""Native Qt checks for persistent song selection and set-list organization."""
import hashlib
from pathlib import Path
import sys
import time

import numpy as np
import pytest
import soundfile as sf

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'source'))

from PySide6.QtCore import QModelIndex, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
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
        pending = bool(window.jobs)
        for key in ('_import_queue', 'import_queue', '_song_queue'):
            pending = pending or bool(getattr(window, key, []))
        if not pending:
            pump(.05)
            return
        time.sleep(.01)
    pytest.fail('Song library jobs did not finish in time')


def fixture_song(path, bpm=120, seconds=12):
    rate = 44100
    samples = np.zeros((seconds * rate, 2), dtype=np.float32)
    t = np.arange(round(.065 * rate)) / rate
    pulse = (np.sin(2 * np.pi * 110 * t) * np.exp(-48 * t) * .6).astype(np.float32)
    for beat in np.arange(.3, seconds - .1, 60 / bpm):
        start = round(beat * rate)
        samples[start:start + len(pulse)] += pulse[:, None]
    sf.write(path, samples, rate)
    return path


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

    def create(library_path=None):
        w = appmod.MainWindow(library_path=library_path or tmp_path / 'library.json')
        w.test_errors = []
        monkeypatch.setattr(w, 'show_error', lambda title, text: w.test_errors.append((title, text)))
        windows.append(w)
        w.show()
        pump(.08)
        return w

    yield create
    for w in windows:
        w.stop()
        wait_jobs(w)
        w.close()
        pump(.05)


def choose_row(w, song_id):
    for row in range(w.library_list.count()):
        item = w.library_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == song_id:
            w.library_list.scrollToItem(item)
            pump(.02)
            rect = w.library_list.visualItemRect(item)
            QTest.mouseClick(w.library_list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
            pump(.04)
            return
    pytest.fail(f'Library row not found: {song_id}')


def test_click_loads_saved_setup_and_edits_survive_restart(make_window, tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    first_path = fixture_song(tmp_path / 'First.wav', 90)
    second_path = fixture_song(tmp_path / 'Second.wav', 150)
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (first_path, second_path)]
    store = SongLibrary(path)
    first = store.add(str(first_path), bpm=91.7, beats_per_bar=3, subdivision=2,
                      accent=False, sound='Beep', volume=.15, offset_seconds=.712,
                      duration_seconds=12, analyzed=True, confidence='High')
    second = store.add(str(second_path), bpm=153.2, beats_per_bar=7, subdivision=4,
                       accent=True, sound='Wood', volume=.25, offset_seconds=.456,
                       duration_seconds=12, analyzed=True, confidence='Medium')
    # Saved rows must use their corrected tempo, rather than analyzing again.
    monkeypatch.setattr(appmod, 'analyze_audio', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Saved song was re-analyzed')))
    w = make_window(path)
    choose_row(w, first['id'])
    wait_jobs(w)
    assert w.active_song_id == first['id']
    assert w.bpm.value() == 91.7
    assert w.meter.currentData() == 3
    assert w.subdivision.currentData() == 2
    assert not w.accent.isChecked()
    assert w.sound.currentText() == 'Beep'
    assert w.offset.value() == .712
    choose_row(w, second['id'])
    wait_jobs(w)
    assert w.bpm.value() == 153.2
    assert w.meter.currentData() == 7
    assert w.subdivision.currentData() == 4
    assert w.offset.value() == .456
    w.bpm.setValue(154.6)
    w.meter.setCurrentIndex(4)
    w.subdivision.setCurrentIndex(0)
    w.accent.setChecked(False)
    w.sound.setCurrentText('Beep')
    w.offset.setValue(.888)
    w.volume.setValue(31)
    # Switching immediately must flush pending saves before a different row loads.
    choose_row(w, first['id'])
    wait_jobs(w)
    choose_row(w, second['id'])
    wait_jobs(w)
    assert w.bpm.value() == 154.6
    assert w.meter.currentData() == 5
    assert w.subdivision.currentData() == 1
    assert not w.accent.isChecked()
    assert w.sound.currentText() == 'Beep'
    assert w.offset.value() == .888
    assert w.volume.value() == 31
    w.close()
    pump(.05)
    reopened = make_window(path)
    choose_row(reopened, second['id'])
    wait_jobs(reopened)
    assert reopened.bpm.value() == 154.6
    assert reopened.meter.currentData() == 5
    assert reopened.offset.value() == .888
    assert hashes == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (first_path, second_path)]
    assert not w.test_errors


def test_reorder_through_list_model_is_durable(make_window, tmp_path):
    path = tmp_path / 'library.json'
    store = SongLibrary(path)
    entries = [store.add(str(tmp_path / f'{name}.wav'), bpm=bpm, analyzed=True)
               for name, bpm in [('Opener', 100), ('Middle', 125), ('Finale', 140)]]
    w = make_window(path)
    assert w.library_list.count() == 3
    assert w.library_list.model().moveRow(QModelIndex(), 0, QModelIndex(), 3)
    pump(.6)
    expected = [entries[1]['id'], entries[2]['id'], entries[0]['id']]
    assert [s['id'] for s in SongLibrary(path).songs] == expected
    assert [w.library_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)] == expected


def test_missing_source_still_loads_click_settings(make_window, tmp_path):
    path = tmp_path / 'library.json'
    entry = SongLibrary(path).add(str(tmp_path / 'Moved away.wav'), bpm=141.2,
        beats_per_bar=6, subdivision=2, volume=.05, offset_seconds=1.234,
        duration_seconds=90, analyzed=True, include_song=False)
    w = make_window(path)
    choose_row(w, entry['id'])
    wait_jobs(w)
    assert w.bpm.value() == 141.2
    assert w.meter.currentData() == 6
    assert w.subdivision.currentData() == 2
    assert w.offset.value() == 1.234
    assert w.audio is None
    w.include_song.setChecked(False)
    w.toggle_play()
    pump(.25)
    assert w.engine.running
    assert w.engine.error is None
    w.stop()
    assert SongLibrary(path).get(entry['id'])['bpm'] == 141.2


def test_import_auto_adds_rows_and_remembers_independent_tempos(make_window, tmp_path):
    w = make_window()
    first = fixture_song(tmp_path / 'Slow song.wav', 90)
    second = fixture_song(tmp_path / 'Fast song.wav', 150)
    w.import_song(str(first))
    wait_jobs(w)
    assert len(w.library.songs) == 1
    first_entry = w.library.songs[0]
    assert abs(first_entry['bpm'] - 90) <= .5
    w.import_song(str(second))
    wait_jobs(w)
    assert len(w.library.songs) == 2
    second_entry = next(song for song in w.library.songs if Path(song['path']) == second)
    assert abs(second_entry['bpm'] - 150) <= .5
    choose_row(w, first_entry['id'])
    wait_jobs(w)
    assert abs(w.bpm.value() - 90) <= .5
    assert w.library.get(second_entry['id'])['bpm'] == second_entry['bpm']


def test_fast_selection_never_applies_previous_audio_to_new_song(make_window, tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    store = SongLibrary(path)
    a = store.add(str(fixture_song(tmp_path / 'A.wav', 100)), bpm=101.1,
                  analyzed=True, include_song=True, duration_seconds=12)
    b = store.add(str(fixture_song(tmp_path / 'B.wav', 150)), bpm=151.7,
                  analyzed=True, include_song=True, duration_seconds=12)
    real_load = appmod.load_audio
    def slow_load(*args, **kwargs):
        time.sleep(.2)
        return real_load(*args, **kwargs)
    monkeypatch.setattr(appmod, 'load_audio', slow_load)
    w = make_window(path)
    class FakeEngine:
        running = False
        error = None
        position_seconds = 0.0
        starts = []
        def start(self, settings, **kwargs):
            self.starts.append((settings, kwargs))
            self.running = True
        def stop(self):
            self.running = False
    w.engine = FakeEngine()
    choose_row(w, a['id'])
    w.toggle_play()
    choose_row(w, b['id'])
    w.toggle_play()
    wait_jobs(w)
    assert w.active_song_id == b['id']
    assert w.bpm.value() == 151.7
    if w.audio is not None:
        assert Path(w.audio.path) == Path(b['path'])
    assert len(w.engine.starts) == 1
    assert Path(w.engine.starts[0][1]['audio'].path) == Path(b['path'])
    assert w.library.get(a['id'])['bpm'] == 101.1


def test_cancel_while_song_decodes_does_not_autoplay(make_window, tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    entry = SongLibrary(path).add(str(fixture_song(tmp_path / 'Later.wav')), bpm=120,
                                 analyzed=True, include_song=True, duration_seconds=12)
    real_load = appmod.load_audio
    def slow_load(*args, **kwargs):
        time.sleep(.2)
        return real_load(*args, **kwargs)
    monkeypatch.setattr(appmod, 'load_audio', slow_load)
    w = make_window(path)
    choose_row(w, entry['id'])
    w.toggle_play()
    assert w._awaiting_song_start is not None
    w.toggle_play()
    wait_jobs(w)
    assert not w.engine.running
    assert w._awaiting_song_start is None


def test_cached_song_export_matches_duration_and_protects_original(make_window, tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    original = fixture_song(tmp_path / 'Original.wav')
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    entry = SongLibrary(path).add(str(original), bpm=137.2, analyzed=True,
                                 duration_seconds=12, offset_seconds=.321)
    w = make_window(path)
    choose_row(w, entry['id'])
    assert w.audio is None
    out = tmp_path / 'click.wav'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(out), 'WAV audio (*.wav)'))
    w.export_wav()
    wait_jobs(w)
    assert sf.info(out).frames == 12 * 44100
    rendered, rate = sf.read(out)
    first_nonzero = int(np.flatnonzero(np.abs(rendered) > .001)[0])
    assert abs(first_nonzero / rate - .321) < .005
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(original), 'WAV audio (*.wav)'))
    w.export_wav()
    wait_jobs(w)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
    assert w.test_errors


def test_rename_up_down_and_remove_preserve_audio(make_window, tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    first_path = fixture_song(tmp_path / 'One.wav', 90)
    second_path = fixture_song(tmp_path / 'Two.wav', 150)
    first_hash = hashlib.sha256(first_path.read_bytes()).hexdigest()
    store = SongLibrary(path)
    one = store.add(str(first_path), bpm=90, analyzed=True)
    two = store.add(str(second_path), bpm=150, analyzed=True)
    w = make_window(path)
    choose_row(w, one['id'])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('Opening number', True))
    QTest.mouseClick(w.library_rename, Qt.MouseButton.LeftButton)
    assert SongLibrary(path).get(one['id'])['title'] == 'Opening number'
    QTest.mouseClick(w.library_down, Qt.MouseButton.LeftButton)
    assert [s['id'] for s in SongLibrary(path).songs] == [two['id'], one['id']]
    assert w.active_song_id == one['id']
    QTest.mouseClick(w.library_up, Qt.MouseButton.LeftButton)
    assert [s['id'] for s in SongLibrary(path).songs] == [one['id'], two['id']]
    QTest.mouseClick(w.library_remove, Qt.MouseButton.LeftButton)
    assert SongLibrary(path).get(one['id']) is None
    assert first_path.is_file()
    assert hashlib.sha256(first_path.read_bytes()).hexdigest() == first_hash


def test_multiselect_batch_skips_bad_duplicates_and_keeps_user_selection(make_window, tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    old = SongLibrary(path).add(str(fixture_song(tmp_path / 'Kept.wav')), bpm=147.3,
                               analyzed=True, duration_seconds=12)
    slow = fixture_song(tmp_path / 'Batch slow.wav', 90)
    fast = fixture_song(tmp_path / 'Batch fast.wav', 150)
    missing = tmp_path / 'Missing.wav'
    real_analyze = appmod.analyze_audio
    def slow_analyze(*args, **kwargs):
        time.sleep(.15)
        return real_analyze(*args, **kwargs)
    monkeypatch.setattr(appmod, 'analyze_audio', slow_analyze)
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *args, **kwargs:
                        ([str(slow), str(missing), str(slow), str(fast)], 'Audio files'))
    w = make_window(path)
    QTest.mouseClick(w.library_add_button, Qt.MouseButton.LeftButton)
    choose_row(w, old['id'])
    wait_jobs(w)
    assert w.active_song_id == old['id']
    assert w.bpm.value() == 147.3
    rows = w.library.songs
    assert len(rows) == 3
    assert [Path(row['path']) for row in rows] == [Path(old['path']), slow, fast]
    assert abs(rows[1]['bpm'] - 90) <= .5
    assert abs(rows[2]['bpm'] - 150) <= .5
    assert w.library_add_button.isEnabled()


def test_no_beat_song_can_be_corrected_and_recalled(make_window, tmp_path):
    silent = tmp_path / 'Quiet song.wav'
    sf.write(silent, np.zeros((44100 * 5, 2), dtype=np.float32), 44100)
    w = make_window()
    w.import_song(str(silent))
    wait_jobs(w)
    assert len(w.library.songs) == 1
    row = w.library.songs[0]
    assert not row['analyzed']
    assert w.active_song_id == row['id']
    w.bpm.setValue(83.4)
    w.meter.setCurrentIndex(2)
    assert w.save_active_song()
    restored = SongLibrary(w.library.path).get(row['id'])
    assert restored['bpm'] == 83.4
    assert restored['confidence'] == 'Manual'
    assert restored['analyzed']
    assert restored['beats_per_bar'] == 3


def test_native_library_layout(make_window, tmp_path):
    path = tmp_path / 'library.json'
    store = SongLibrary(path)
    for n, title in enumerate(['First song', 'Middle song with a much longer descriptive title', 'Final song']):
        store.add(str(tmp_path / (title + '.wav')), bpm=90 + n * 20, analyzed=True)
    w = make_window(path)
    out = BASE / 'work' / 'qa'
    out.mkdir(exist_ok=True)
    w.grab().save(str(out / 'library_normal.png'))
    assert w.library_list.isVisible()
    assert w.library_list.width() >= 200
    assert w.bpm.lineEdit().width() > 100
    assert w.centralWidget().horizontalScrollBar().maximum() == 0
    w.resize(w.minimumWidth(), w.minimumHeight())
    pump(.1)
    w.grab().save(str(out / 'library_minimum.png'))
    assert w.centralWidget().horizontalScrollBar().maximum() == 0
