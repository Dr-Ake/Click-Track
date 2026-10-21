import json
import os
from pathlib import Path
import sys

import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "source"))
import song_library as module
from song_library import SongLibrary


def test_reopen_preserves_all_settings_and_order(tmp_path):
    path = tmp_path / "library.json"
    library = SongLibrary(path)
    one = library.add(tmp_path / "One.mp3", bpm=95.4, beats_per_bar=3,
                      subdivision=2, accent=False, volume=.33, sound="Beep",
                      offset_seconds=1.234, duration_seconds=164.8,
                      confidence="Medium", analyzed=True, include_song=True,
                      match_song_length=False)
    two = library.add(tmp_path / "Two.wav", title="Encore", bpm=174)
    library.reorder([two["id"], one["id"]])
    reopened = SongLibrary(path)
    assert reopened.error is None
    assert reopened.songs == [two, one]
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_updates_persist_and_return_defensive_copies(tmp_path):
    library = SongLibrary(tmp_path / "library.json")
    song = library.add(tmp_path / "Beat.mp3")
    result = library.update(song["id"], bpm=131.2, title="Corrected", offset_seconds=.35)
    result["bpm"] = 20
    song["title"] = "Mutated"
    rows = library.songs
    rows[0]["bpm"] = 30
    read = library.get(song["id"])
    read["sound"] = "Invalid"
    actual = SongLibrary(library.path).get(song["id"])
    assert actual["bpm"] == 131.2
    assert actual["title"] == "Corrected"
    assert actual["sound"] == "Wood"


def test_duplicate_paths_update_in_place_and_preserve_title(tmp_path):
    library = SongLibrary(tmp_path / "library.json")
    first = library.add(tmp_path / "music" / "Song.wav", title="Saved title", bpm=96)
    variant = tmp_path / "music" / "unused" / ".." / "Song.wav"
    second = library.add(variant, bpm=97)
    assert second["id"] == first["id"]
    assert second["title"] == "Saved title"
    assert len(library.songs) == 1
    assert library.add(variant)["bpm"] == 97
    if os.name == "nt":
        assert library.add(str(tmp_path / "MUSIC" / "SONG.WAV"))["id"] == first["id"]


def test_removing_entry_never_deletes_audio(tmp_path):
    audio = tmp_path / "original.wav"
    audio.write_bytes(b"original music")
    library = SongLibrary(tmp_path / "library.json")
    song = library.add(audio)
    library.remove(song["id"])
    assert audio.read_bytes() == b"original music"
    assert SongLibrary(library.path).songs == []
    assert library.get(song["id"]) is None


@pytest.mark.parametrize("content", [
    "not json",
    '{"version": 2, "songs": []}',
    '{"version": 1, "songs": [{"title": "broken"}]}',
    '{"version": true, "songs": []}',
    '{"version": 1, "songs": [], "songs": []}',
])
def test_corrupt_file_is_preserved_and_writes_blocked(tmp_path, content):
    path = tmp_path / "library.json"
    path.write_text(content, encoding="utf-8")
    library = SongLibrary(path)
    assert library.error
    with pytest.raises(ValueError, match="preserved"):
        library.add(tmp_path / "new.wav")
    with pytest.raises(ValueError):
        library.save()
    assert path.read_text(encoding="utf-8") == content


def test_non_utf8_file_is_preserved_and_blocked(tmp_path):
    path = tmp_path / "library.json"
    path.write_bytes(b"\xff\xfe\x80 damaged library")
    original = path.read_bytes()
    library = SongLibrary(path)
    assert library.error
    with pytest.raises(ValueError):
        library.add(tmp_path / "New.wav")
    assert path.read_bytes() == original


def test_disk_failure_rolls_back_every_mutation(tmp_path, monkeypatch):
    library = SongLibrary(tmp_path / "library.json")
    a = library.add(tmp_path / "One.wav")
    b = library.add(tmp_path / "Two.wav")
    before = library.songs
    data = library.path.read_bytes()
    def deny(*args, **kwargs):
        raise PermissionError("Disk is read-only")
    monkeypatch.setattr(module.os, "replace", deny)
    for action in [lambda: library.add(tmp_path / "Three.wav"),
                   lambda: library.update(a["id"], bpm=70),
                   lambda: library.remove(a["id"]),
                   lambda: library.reorder([b["id"], a["id"]])]:
        with pytest.raises(OSError, match="could not be saved"):
            action()
        assert library.songs == before
        assert library.path.read_bytes() == data
        assert not list(tmp_path.glob(".library-*"))


def test_read_failure_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "library.json"
    path.write_text("preserve me", encoding="utf-8")
    original = Path.read_bytes
    def deny(self, *args, **kwargs):
        if self == path:
            raise PermissionError("Unreadable")
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", deny)
        library = SongLibrary(path)
    assert library.error
    with pytest.raises(ValueError):
        library.add(tmp_path / "New.wav")
    assert path.read_text(encoding="utf-8") == "preserve me"


@pytest.mark.parametrize("field,value", [
    ("bpm", 301), ("bpm", float("nan")), ("volume", -1),
    ("subdivision", 3), ("beats_per_bar", True), ("accent", 1),
    ("duration_seconds", 1801), ("offset_seconds", -1),
    ("sound", "Unknown"), ("title", " "), ("id", "overwrite-id"),
    ("unknown_setting", 4),
])
def test_invalid_updates_leave_file_and_memory_unchanged(tmp_path, field, value):
    library = SongLibrary(tmp_path / "library.json")
    song = library.add(tmp_path / "Song.wav")
    before = library.path.read_bytes()
    with pytest.raises(ValueError):
        library.update(song["id"], **{field: value})
    assert library.songs == [song]
    assert library.path.read_bytes() == before


def test_reorder_requires_exact_permutation(tmp_path):
    library = SongLibrary(tmp_path / "library.json")
    a = library.add(tmp_path / "One.wav")
    b = library.add(tmp_path / "Two.wav")
    for bad in [[a["id"]], [a["id"], a["id"]], [b["id"], "unknown"], [[1], [2]]]:
        with pytest.raises(ValueError):
            library.reorder(bad)
        assert library.songs == [a, b]


def test_relocating_to_an_existing_entry_is_rejected(tmp_path):
    library = SongLibrary(tmp_path / "library.json")
    a = library.add(tmp_path / "One.wav")
    b = library.add(tmp_path / "Two.wav")
    with pytest.raises(ValueError, match="more than once"):
        library.update(a["id"], path=b["path"])
    assert library.songs == [a, b]


def test_default_location_uses_project_data(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "data_directory", lambda: tmp_path / "data")
    library = SongLibrary()
    assert library.path == tmp_path / "data" / "library.json"
    assert not library.path.exists()


def test_two_new_instances_do_not_overwrite_first_saved_library(tmp_path):
    path = tmp_path / "library.json"
    first = SongLibrary(path)
    stale = SongLibrary(path)
    song = first.add(tmp_path / "First.wav")
    saved = path.read_bytes()
    with pytest.raises(OSError, match="Library changed in another window"):
        stale.add(tmp_path / "Other.wav")
    assert stale.songs == []
    assert path.read_bytes() == saved
    assert SongLibrary(path).songs == [song]


def test_concurrent_existing_instances_preserve_newer_additions(tmp_path):
    path = tmp_path / "library.json"
    first = SongLibrary(path)
    song = first.add(tmp_path / "First.wav")
    stale = SongLibrary(path)
    newer = first.add(tmp_path / "New addition.wav", bpm=145)
    saved = path.read_bytes()
    with pytest.raises(OSError, match="Reopen ClickTrack before editing"):
        stale.update(song["id"], bpm=90)
    assert stale.songs == [song]
    assert path.read_bytes() == saved
    assert SongLibrary(path).songs == [song, newer]


def test_external_deletion_does_not_recreate_stale_library(tmp_path):
    library = SongLibrary(tmp_path / "library.json")
    song = library.add(tmp_path / "First.wav")
    library.path.unlink()
    with pytest.raises(OSError, match="Library changed in another window"):
        library.update(song["id"], title="Changed")
    assert not library.path.exists()
    assert library.songs == [song]


def test_change_during_temporary_write_is_detected(tmp_path, monkeypatch):
    library = SongLibrary(tmp_path / "library.json")
    song = library.add(tmp_path / "First.wav")
    original_fsync = module.os.fsync
    changed = b'{"version":1,"songs":[]}\n'
    def simultaneous_write(fd):
        original_fsync(fd)
        library.path.write_bytes(changed)
    monkeypatch.setattr(module.os, "fsync", simultaneous_write)
    with pytest.raises(OSError, match="Library changed in another window"):
        library.update(song["id"], bpm=200)
    assert library.path.read_bytes() == changed
    assert library.songs == [song]
    assert not list(tmp_path.glob(".library-*"))
