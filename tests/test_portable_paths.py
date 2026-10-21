"""Portable project location checks; every writable path belongs to pytest temp."""
from pathlib import Path
import json
import os
import sys
import tempfile
import uuid

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'source'))

import app_paths
from song_library import SongLibrary


@pytest.fixture
def portable_root(tmp_path, monkeypatch):
    root = tmp_path / 'Portable ClickTrack'
    (root / 'source').mkdir(parents=True)
    monkeypatch.setattr(app_paths, '__file__', str(root / 'source' / 'app_paths.py'))
    monkeypatch.delattr(sys, 'frozen', raising=False)
    monkeypatch.delattr(sys, '_MEIPASS', raising=False)
    return root


def test_source_root_comes_from_module_not_working_directory(portable_root, tmp_path, monkeypatch):
    unrelated = tmp_path / 'unrelated directory'
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    assert app_paths.project_root() == portable_root
    assert app_paths.data_directory() == portable_root / 'data'


def test_frozen_root_comes_from_executable_not_bundle_directory(portable_root, tmp_path, monkeypatch):
    frozen_root = tmp_path / 'Installed ClickTrack'
    (frozen_root / 'app' / '_internal').mkdir(parents=True)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(frozen_root / 'app' / 'ClickTrack.exe'))
    monkeypatch.setattr(sys, '_MEIPASS', str(frozen_root / 'app' / '_internal'), raising=False)
    monkeypatch.chdir(tmp_path)
    assert app_paths.project_root() == frozen_root
    assert app_paths.data_directory() == frozen_root / 'data'


def test_default_library_lives_in_portable_data(portable_root, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    store = SongLibrary()
    assert store.path == portable_root / 'data' / 'library.json'
    song = store.add(str(tmp_path / 'original audio.wav'), bpm=137.3, analyzed=True)
    assert store.path.is_file()
    reopened = SongLibrary()
    assert reopened.get(song['id'])['bpm'] == 137.3
    assert not (tmp_path / 'original audio.wav').exists()


def test_explicit_library_path_override_remains_independent(portable_root, tmp_path):
    explicit = tmp_path / 'separate library' / 'practice.json'
    store = SongLibrary(explicit)
    entry = store.add(str(tmp_path / 'source.wav'), bpm=89.1, analyzed=True)
    assert store.path == explicit
    assert SongLibrary(explicit).get(entry['id'])['bpm'] == 89.1
    assert not (portable_root / 'data' / 'library.json').exists()


def test_runtime_temporary_files_are_project_local_and_process_only(portable_root, tmp_path, monkeypatch):
    old_temp = tmp_path / 'old temp'
    old_temp.mkdir()
    monkeypatch.setenv('TEMP', str(old_temp))
    monkeypatch.setenv('TMP', str(old_temp))
    monkeypatch.setattr(tempfile, 'tempdir', None)
    app_paths.configure_runtime_temp()
    expected = portable_root / 'work' / 'temp'
    assert expected.is_dir()
    assert Path(os.environ['TEMP']) == expected
    assert Path(os.environ['TMP']) == expected
    assert Path(tempfile.gettempdir()) == expected
    with tempfile.TemporaryFile() as stream:
        stream.write(b'local scratch')
    assert old_temp.is_dir()


@pytest.fixture(scope='session')
def qtapp():
    from PySide6.QtWidgets import QApplication
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    return instance


def test_window_uses_portable_ini_and_preserves_all_settings(portable_root, tmp_path, qtapp):
    from PySide6.QtCore import QSettings
    import app
    data = app_paths.data_directory()
    data.mkdir(parents=True, exist_ok=True)
    settings_path = data / 'settings.ini'
    initial = QSettings(str(settings_path), QSettings.Format.IniFormat)
    initial.setValue('future_custom_key', 'keep this setting')
    initial.setValue('snap_to_beat', False)
    initial.setValue('practice_regions', json.dumps({'sample-id': {'start': 2.3, 'end': 3.3, 'enabled': True}}))
    initial.sync()
    window = app.MainWindow(library_path=tmp_path / 'test library.json')
    try:
        assert Path(window.preferences.fileName()) == settings_path
        assert window.preferences.format() == QSettings.Format.IniFormat
        assert not window.transport.snap_checkbox.isChecked()
        assert window.preferences.value('future_custom_key') == 'keep this setting'
    finally:
        window.stop()
        window.close()
        window.timer.stop()
        window.deleteLater()
        qtapp.processEvents()
    reopened = QSettings(str(settings_path), QSettings.Format.IniFormat)
    assert reopened.value('future_custom_key') == 'keep this setting'
    assert json.loads(reopened.value('practice_regions'))['sample-id']['start'] == 2.3


def test_saved_loop_regions_restore_from_portable_ini(portable_root, tmp_path, qtapp):
    from PySide6.QtCore import QSettings
    import app
    data = app_paths.data_directory()
    data.mkdir(parents=True, exist_ok=True)
    store = SongLibrary(data / 'library.json')
    source = tmp_path / 'external song.wav'
    entry = store.add(str(source), bpm=120, offset_seconds=.3, duration_seconds=12, analyzed=True)
    initial = QSettings(str(data / 'settings.ini'), QSettings.Format.IniFormat)
    initial.setValue('practice_regions', json.dumps({entry['id']: {'start': 2.3, 'end': 5.3, 'enabled': True}}))
    initial.sync()
    window = app.MainWindow()
    try:
        window.load_library_song(entry['id'])
        assert window.transport.loop_enabled
        assert window.transport.loop_start_seconds == pytest.approx(2.3)
        assert window.transport.loop_end_seconds == pytest.approx(5.3)
        assert window.library.get(entry['id'])['path'] == str(source.resolve())
    finally:
        window.stop()
        window.close()
        window.timer.stop()
        window.deleteLater()
        qtapp.processEvents()
    assert not source.exists()


def test_source_run_uses_bundled_decoder_without_path_dependency(portable_root, monkeypatch):
    import audio_core
    decoder = portable_root / 'app' / '_internal' / 'ffmpeg.exe'
    decoder.parent.mkdir(parents=True)
    decoder.write_bytes(b'placeholder; finder must not execute it')
    monkeypatch.setattr(audio_core, '__file__', str(portable_root / 'source' / 'audio_core.py'))
    monkeypatch.setattr(audio_core.shutil, 'which', lambda command: None)
    assert Path(audio_core._ffmpeg_path()) == decoder
