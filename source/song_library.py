"""Durable, ordered song presets. This module never changes song audio files."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
from app_paths import data_directory


DEFAULTS = {
    "bpm": 120.0,
    "beats_per_bar": 4,
    "subdivision": 1,
    "accent": True,
    "volume": 0.65,
    "sound": "Wood",
    "offset_seconds": 0.0,
    "duration_seconds": 0.0,
    "confidence": "Not analyzed",
    "analyzed": False,
    "include_song": False,
    "match_song_length": True,
}
FIELDS = frozenset({"id", "title", "path", *DEFAULTS})
EDITABLE_FIELDS = FIELDS - {"id"}


def _normalized_path(path):
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError("Choose a valid song file path.")
    value = os.fspath(path)
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("Choose a valid song file path.")
    return str(Path(value).expanduser().resolve(strict=False))


def _path_key(path):
    return os.path.normcase(os.path.normpath(path))


def _number(value, low, high, description):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be a number.")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{description} must be between {low:g} and {high:g}.")
    return result


def _validate_entry(entry):
    if not isinstance(entry, dict) or set(entry) != FIELDS:
        raise ValueError("A song preset has missing or unrecognized fields.")
    item = deepcopy(entry)
    if not isinstance(item["id"], str):
        raise ValueError("A song preset has an invalid identifier.")
    try:
        item["id"] = str(uuid.UUID(item["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("A song preset has an invalid identifier.") from exc
    if not isinstance(item["title"], str) or not item["title"].strip():
        raise ValueError("Give the song a name.")
    if len(item["title"]) > 512 or "\0" in item["title"]:
        raise ValueError("The song name is too long or contains invalid characters.")
    item["title"] = item["title"].strip()
    if not isinstance(item["path"], str) or not Path(item["path"]).is_absolute():
        raise ValueError("A song preset must have an absolute file path.")
    item["path"] = _normalized_path(item["path"])
    for name, low, high, text in [
        ("bpm", 20, 300, "Tempo"),
        ("volume", 0, 1, "Volume"),
        ("offset_seconds", 0, 1800, "First-click offset"),
        ("duration_seconds", 0, 1800, "Song length"),
    ]:
        item[name] = _number(item[name], low, high, text)
    for name, allowed, text in [
        ("beats_per_bar", range(1, 13), "Beats per bar"),
        ("subdivision", (1, 2, 4), "Subdivision"),
    ]:
        if isinstance(item[name], bool) or not isinstance(item[name], int) or item[name] not in allowed:
            raise ValueError(f"{text} has an invalid value.")
    for name in ("accent", "analyzed", "include_song", "match_song_length"):
        if not isinstance(item[name], bool):
            raise ValueError(f"{name.replace('_', ' ').capitalize()} must be on or off.")
    if item["sound"] not in ("Wood", "Beep"):
        raise ValueError("Choose the Wood or Beep click sound.")
    if not isinstance(item["confidence"], str) or len(item["confidence"]) > 256:
        raise ValueError("The tempo confidence label is invalid.")
    return item


def _validate_songs(songs):
    if not isinstance(songs, list):
        raise ValueError("The library song list is invalid.")
    validated = [_validate_entry(item) for item in songs]
    ids = [item["id"] for item in validated]
    paths = [_path_key(item["path"]) for item in validated]
    if len(ids) != len(set(ids)):
        raise ValueError("The library contains repeated song identifiers.")
    if len(paths) != len(set(paths)):
        raise ValueError("The library contains the same song file more than once.")
    return validated


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("The library contains repeated data fields.")
        result[key] = value
    return result


class SongLibrary:
    """An ordered collection persisted after every successful change.

    ``songs`` and method results are defensive copies. Malformed files are kept
    intact; ``error`` explains why writes are blocked until the original file is
    repaired or moved by the user. An ordinary write failure leaves both the
    previous file and in-memory collection intact, and can be retried. A content
    fingerprint prevents a stale window from overwriting another window's edits.
    """

    def __init__(self, path=None):
        default = data_directory() / "library.json"
        self.path = Path(path).expanduser().resolve(strict=False) if path is not None else default
        self.error = None
        self._songs = []
        self._blocked = False
        self._fingerprint = None
        self._load()

    @property
    def songs(self):
        return deepcopy(self._songs)

    def _load(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except (OSError, UnicodeError) as exc:
            self._blocked = True
            self.error = f"The song library could not be read: {exc}. The existing file has been preserved."
            return
        try:
            self._fingerprint = hashlib.sha256(raw).digest()
            content = raw.decode("utf-8")
            data = json.loads(content, object_pairs_hook=_unique_object)
            if not isinstance(data, dict) or set(data) != {"version", "songs"}:
                raise ValueError("The library format is not recognized.")
            if type(data["version"]) is not int or data["version"] != 1:
                raise ValueError("This library was saved in an unsupported format version.")
            self._songs = _validate_songs(data["songs"])
        except (ValueError, TypeError) as exc:
            self._blocked = True
            self.error = f"The song library could not be loaded: {exc} The original file is preserved at {self.path}."

    def get(self, song_id):
        for item in self._songs:
            if item["id"] == song_id:
                return deepcopy(item)
        return None

    def _index(self, song_id):
        for index, item in enumerate(self._songs):
            if item["id"] == song_id:
                return index
        raise KeyError("That song is no longer in the library.")

    def _ensure_writable(self):
        if self._blocked:
            raise ValueError(self.error)

    def _ensure_unchanged(self):
        try:
            fingerprint = hashlib.sha256(self.path.read_bytes()).digest()
        except FileNotFoundError:
            fingerprint = None
        if fingerprint != self._fingerprint:
            raise OSError("Library changed in another window. Reopen ClickTrack before editing.")

    def _commit(self, songs):
        self._ensure_writable()
        validated = _validate_songs(songs)
        payload = json.dumps({"version": 1, "songs": validated}, ensure_ascii=False,
                             indent=2, allow_nan=False) + "\n"
        temporary = None
        try:
            self._ensure_unchanged()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".library-", suffix=".json.tmp",
                                                      dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._ensure_unchanged()
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            raise OSError(f"The song library could not be saved: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
        self._songs = validated
        self._fingerprint = hashlib.sha256(payload.encode("utf-8")).digest()
        self.error = None

    def add(self, path, title=None, **settings):
        self._ensure_writable()
        unknown = set(settings) - set(DEFAULTS)
        if unknown:
            raise ValueError("Unrecognized song settings: " + ", ".join(sorted(unknown)))
        path = _normalized_path(path)
        key = _path_key(path)
        for existing in self._songs:
            if _path_key(existing["path"]) == key:
                updates = dict(settings)
                if title is not None:
                    updates["title"] = title
                return self.update(existing["id"], **updates) if updates else deepcopy(existing)
        entry = {"id": str(uuid.uuid4()), "title": title if title is not None else Path(path).stem,
                 "path": path, **DEFAULTS, **settings}
        entry = _validate_entry(entry)
        self._commit([*self._songs, entry])
        return deepcopy(entry)

    def update(self, song_id, **fields):
        self._ensure_writable()
        unknown = set(fields) - EDITABLE_FIELDS
        if unknown:
            raise ValueError("Unrecognized song settings: " + ", ".join(sorted(unknown)))
        index = self._index(song_id)
        if "path" in fields:
            fields["path"] = _normalized_path(fields["path"])
        songs = deepcopy(self._songs)
        songs[index].update(fields)
        songs[index] = _validate_entry(songs[index])
        self._commit(songs)
        return deepcopy(songs[index])

    def remove(self, song_id):
        self._ensure_writable()
        index = self._index(song_id)
        songs = deepcopy(self._songs)
        removed = songs.pop(index)
        self._commit(songs)
        return deepcopy(removed)

    def reorder(self, song_ids):
        self._ensure_writable()
        order = list(song_ids)
        existing = {item["id"]: item for item in self._songs}
        try:
            valid = len(order) == len(existing) and len(set(order)) == len(order) and set(order) == set(existing)
        except TypeError:
            valid = False
        if not valid:
            raise ValueError("The new order must include every song exactly once.")
        self._commit([existing[song_id] for song_id in order])

    def save(self):
        """Explicitly persist the current order; mutations already call this logic."""
        self._commit(self._songs)
