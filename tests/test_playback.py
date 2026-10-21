import sys
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
import playback
from audio_core import AudioData, ClickSettings, render_clicks


def test_chunks_exactly_match_one_block():
    settings = ClickSettings(bpm=137.3, subdivision=4, offset_seconds=0.317, volume=0.4)
    whole_state = playback._make_state(settings, None, False, None)
    whole = np.empty((100_003, 2), dtype=np.float32)
    playback._fill_output(whole_state, whole, len(whole))
    state = playback._make_state(settings, None, False, None)
    chunks = []
    for frames in (1, 1024, 305, 75, 44100, 1024, 53474):
        chunk = np.empty((frames, 2), dtype=np.float32)
        playback._fill_output(state, chunk, frames)
        chunks.append(chunk)
    assert sum(map(len, chunks)) == len(whole)
    np.testing.assert_array_equal(whole, np.concatenate(chunks))


def test_song_mix_bounds_and_exact_end():
    song = SimpleNamespace(sample_rate=44100, samples=np.ones((1234, 2), np.float32))
    state = playback._make_state(ClickSettings(volume=1), song, True, None)
    out = np.empty((2000, 2), np.float32)
    assert playback._fill_output(state, out, len(out)) is True
    assert np.max(np.abs(out)) <= 0.98
    assert np.any(out[:1234])
    assert np.count_nonzero(out[1234:]) == 0
    assert state.frame == 1234


@pytest.mark.parametrize("sound", ["Wood", "Beep"])
def test_playback_matches_export_render(sound):
    settings = ClickSettings(bpm=173.9, subdivision=4, offset_seconds=.31791,
                             beats_per_bar=3, volume=.7, sound=sound)
    state = playback._make_state(settings, None, False, None)
    out = np.empty((100_003, 2), np.float32)
    playback._fill_output(state, out, len(out))
    expected = render_clicks(settings, len(out) / 44100, 44100)
    np.testing.assert_allclose(out[:, 0], expected, atol=1e-7)
    np.testing.assert_array_equal(out[:, 0], out[:, 1])


def test_duration_and_bad_parameters():
    state = playback._make_state(ClickSettings(), None, False, .05)
    assert state.limit == 2205
    with pytest.raises(ValueError):
        playback._make_state(ClickSettings(bpm=float("nan")), None, False, None)
    with pytest.raises(ValueError):
        playback._make_state(ClickSettings(), None, True, None)


class FakeStream:
    instances = []
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.latency = .1
        self.time = 100.
        self.closed = self.aborted = False
        self.instances.append(self)
    def start(self):
        self.active = True
    def abort(self):
        self.aborted = True
        self.active = False
    def close(self):
        self.closed = True


def test_start_position_stop_restart(monkeypatch):
    monkeypatch.setattr(playback.sd, "OutputStream", FakeStream)
    engine = playback.PlaybackEngine()
    engine.start(ClickSettings())
    stream = engine._stream
    assert engine.running
    out = np.empty((1024, 2), np.float32)
    stream.kwargs["callback"](out, 1024, SimpleNamespace(outputBufferDacTime=100.1), None)
    stream.time = 100.6
    assert engine.position_seconds == pytest.approx(.5)
    engine.start(ClickSettings(bpm=90))
    assert stream.closed and stream.aborted
    assert engine.position_seconds == 0
    engine.stop()
    assert not engine.running and engine._stream is None
    engine.stop()


def test_callback_error_is_exposed(monkeypatch):
    monkeypatch.setattr(playback.sd, "OutputStream", FakeStream)
    engine = playback.PlaybackEngine()
    engine.start(ClickSettings())
    def broken(*args):
        raise RuntimeError("test render failure")
    monkeypatch.setattr(playback, "_fill_output", broken)
    out = np.ones((1024, 2), np.float32)
    with pytest.raises(playback.sd.CallbackAbort):
        engine._stream.kwargs["callback"](out, 1024, SimpleNamespace(outputBufferDacTime=100.1), None)
    assert "test render failure" in engine.error
    assert not engine.running
    assert np.count_nonzero(out) == 0
    engine.stop()


def test_device_error_is_friendly(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("No output device")
    monkeypatch.setattr(playback.sd, "OutputStream", broken)
    engine = playback.PlaybackEngine()
    with pytest.raises(RuntimeError, match="speakers or headphones"):
        engine.start(ClickSettings())
    assert not engine.running
    assert "No output device" in engine.error


def test_finite_callback_sets_final_position(monkeypatch):
    monkeypatch.setattr(playback.sd, "OutputStream", FakeStream)
    engine = playback.PlaybackEngine()
    engine.start(ClickSettings(), duration_seconds=.01)
    stream = engine._stream
    out = np.empty((1024, 2), np.float32)
    with pytest.raises(playback.sd.CallbackStop):
        stream.kwargs["callback"](out, 1024, SimpleNamespace(outputBufferDacTime=100.1), None)
    stream.kwargs["finished_callback"]()
    assert not engine.running
    assert engine.position_seconds == pytest.approx(.01)
    engine.stop()


if __name__ == "__main__":
    engine = playback.PlaybackEngine()
    engine.start(ClickSettings(bpm=120, volume=.15), duration_seconds=2.)
    deadline = time.monotonic() + 6
    while engine.running and time.monotonic() < deadline:
        time.sleep(.1)
    print({"audio_output_completed": not engine.running, "position_seconds": engine.position_seconds,
           "error": engine.error, "latency_seconds": engine.latency_seconds})
    engine.stop()
