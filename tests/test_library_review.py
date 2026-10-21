"""Regression checks for library persistence and original-song protection."""
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_library_ui import qtapp, make_window, pump, fixture_song, choose_row
from PySide6.QtWidgets import QFileDialog
from song_library import SongLibrary


def test_missing_audio_retains_song_playback_preference_and_reconnects(make_window, tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    missing = tmp_path / 'Moved song.wav'
    entry = SongLibrary(path).add(missing, bpm=141.2, analyzed=True,
        include_song=True, duration_seconds=12, offset_seconds=.345)
    window = make_window(path)
    choose_row(window, entry['id'])
    assert not window.include_song.isEnabled()
    assert not window.include_song.isChecked()
    window.bpm.setValue(143.7)
    assert window.save_active_song()
    saved = SongLibrary(path).get(entry['id'])
    assert saved['include_song'] is True
    assert saved['bpm'] == 143.7

    replacement = fixture_song(tmp_path / 'Found song.wav', bpm=140)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName',
        lambda *a, **kw: (str(replacement), 'WAV audio (*.wav)'))
    window.locate_library_song()
    assert window.include_song.isEnabled()
    assert window.include_song.isChecked()
    assert window.bpm.value() == 143.7
    assert window.offset.value() == .345
    saved = SongLibrary(path).get(entry['id'])
    assert Path(saved['path']) == replacement
    assert saved['include_song'] is True
    assert not window.test_errors


def test_external_change_allows_close_and_saves_separate_recovery(make_window, tmp_path):
    path = tmp_path / 'library.json'
    entry = SongLibrary(path).add(tmp_path / 'First.wav', bpm=100,
        analyzed=True, duration_seconds=12, include_song=True)
    window = make_window(path)
    choose_row(window, entry['id'])
    external = SongLibrary(path)
    new_entry = external.add(tmp_path / 'Another window.wav', bpm=155, analyzed=True)
    preserved_main = path.read_bytes()
    window.bpm.setValue(103.4)
    assert not window.save_active_song()
    assert 'another window' in window.library_note.text()
    window.close()
    pump(.05)
    assert not window.isVisible(), 'Save conflict must not trap the window open'
    assert path.read_bytes() == preserved_main
    assert SongLibrary(path).get(new_entry['id'])['bpm'] == 155
    recoveries = list(tmp_path.glob('library-recovery-*.json'))
    assert len(recoveries) == 1
    recovered = SongLibrary(recoveries[0])
    assert recovered.error is None
    assert recovered.get(entry['id'])['bpm'] == 103.4
    assert recovered.get(entry['id'])['confidence'] == 'Manual'
    assert recovered.get(entry['id'])['include_song'] is True
    assert window.test_errors[0][0] == 'Song changes saved separately'


def test_export_cannot_replace_a_different_library_songs_original(make_window, tmp_path, monkeypatch):
    first_path = fixture_song(tmp_path / 'First original.wav', bpm=90)
    other_path = fixture_song(tmp_path / 'Other original.wav', bpm=150)
    path = tmp_path / 'library.json'
    store = SongLibrary(path)
    first = store.add(first_path, bpm=90, analyzed=True, duration_seconds=12)
    store.add(other_path, bpm=150, analyzed=True, duration_seconds=12)
    before = {p: hashlib.sha256(p.read_bytes()).digest() for p in (first_path, other_path)}
    window = make_window(path)
    choose_row(window, first['id'])
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
        lambda *a, **kw: (str(other_path), 'WAV audio (*.wav)'))
    window.export_wav()
    assert not window.jobs, 'Export must be blocked before writing to an original song'
    assert window.test_errors == [('Choose a new filename',
        'Save your click track separately from the original song.')]
    assert before == {p: hashlib.sha256(p.read_bytes()).digest() for p in (first_path, other_path)}
