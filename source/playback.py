"""Sample-accurate click and optional song playback through Windows audio."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import sounddevice as sd

from audio_core import AudioData, ClickSettings, click_waveform


@dataclass
class _State:
    sample_rate: int
    period: float
    offset: float
    beats_per_bar: int
    subdivision: int
    volume: float
    accent: bool
    normal: np.ndarray
    strong: np.ndarray
    song: np.ndarray | None
    limit: int | None
    frame: int = 0
    origin_frame: int = 0
    loop_start: int | None = None
    loop_end: int | None = None
    first_dac_time: float | None = None
    last_position: float = 0.0


def _make_state(settings, audio, include_song, duration_seconds,
                start_seconds=0.0, loop_start_seconds=None, loop_end_seconds=None):
    bpm = float(settings.bpm)
    volume = float(settings.volume)
    offset = float(settings.offset_seconds)
    if not math.isfinite(bpm) or not 20 <= bpm <= 300:
        raise ValueError("Tempo must be between 20 and 300 BPM.")
    if not math.isfinite(volume) or not 0 <= volume <= 1:
        raise ValueError("Click volume must be between 0 and 100%.")
    if not math.isfinite(offset) or offset < 0:
        raise ValueError("The first-beat offset cannot be negative.")
    beats = int(settings.beats_per_bar)
    subdivision = int(settings.subdivision)
    if beats != settings.beats_per_bar or not 1 <= beats <= 16:
        raise ValueError("Choose between 1 and 16 beats per bar.")
    if subdivision not in (1, 2, 4):
        raise ValueError("Choose quarter notes, eighth notes, or sixteenth notes.")
    if settings.sound not in ("Wood", "Beep"):
        raise ValueError("Choose the Wood or Beep click sound.")
    rate = 44100
    song = None
    limit = None
    if include_song:
        if audio is None:
            raise ValueError("Import a song before enabling song playback.")
        if int(audio.sample_rate) != rate:
            raise ValueError("The song must be decoded at 44,100 Hz before playback.")
        song = np.asarray(audio.samples, dtype=np.float32)
        if song.ndim != 2 or song.shape[1] != 2 or not len(song):
            raise ValueError("The imported song has no playable stereo audio.")
        limit = len(song)
    if duration_seconds is not None:
        seconds = float(duration_seconds)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Playback duration must be greater than zero.")
        requested = max(1, round(seconds * rate))
        limit = requested if limit is None else min(limit, requested)
    start = float(start_seconds)
    if not math.isfinite(start) or start < 0:
        raise ValueError("Choose a song position at or after zero.")
    start_frame = round(start * rate)
    loop_start = loop_end = None
    if (loop_start_seconds is None) != (loop_end_seconds is None):
        raise ValueError("Set both the loop start and loop end.")
    if loop_start_seconds is not None:
        left, right = float(loop_start_seconds), float(loop_end_seconds)
        if not math.isfinite(left) or not math.isfinite(right) or left < 0 or right <= left:
            raise ValueError("Place the loop end after the loop start.")
        if limit is None:
            raise ValueError("Choose a song or a finite track length before looping.")
        loop_start, loop_end = round(left * rate), round(right * rate)
        if loop_end <= loop_start:
            raise ValueError("Make the practice loop at least one audio sample long.")
        if loop_end > limit:
            raise ValueError("The practice loop must stay within the song.")
        if start_frame < loop_start or start_frame >= loop_end:
            start_frame = loop_start
    elif limit is not None and start_frame > limit:
        raise ValueError("Choose a start position within the song.")
    normal = click_waveform(rate, accent=False, sound=settings.sound)
    strong = click_waveform(rate, accent=True, sound=settings.sound)
    return _State(rate, rate * 60 / (bpm * subdivision), offset * rate,
                  beats, subdivision, volume, bool(settings.accent), normal,
                  strong, song, limit, frame=start_frame, origin_frame=start_frame,
                  loop_start=loop_start, loop_end=loop_end,
                  last_position=start_frame / rate)


def _render_segment(state: _State, outdata: np.ndarray, start: int, count: int):
    """Render an absolute timeline slice into an already-zeroed output view."""
    effective_end = start + count
    if state.song is not None and count:
        np.multiply(state.song[start:effective_end], 0.68, out=outdata[:count])
    tail = max(len(state.normal), len(state.strong))
    first_event = max(0, math.floor((start - state.offset - tail) / state.period) - 1)
    event = first_event
    while count:
        event_frame = round(state.offset + event * state.period)
        if event_frame >= effective_end:
            break
        main_beat = event % state.subdivision == 0
        downbeat = event % (state.subdivision * state.beats_per_bar) == 0
        wave = state.strong if state.accent and downbeat else state.normal
        left = max(start, event_frame)
        right = min(effective_end, event_frame + len(wave))
        if right > left:
            gain = state.volume * (1.0 if main_beat else 0.58)
            chunk = wave[left - event_frame:right - event_frame] * gain
            outdata[left - start:right - start, 0] += chunk
            outdata[left - start:right - start, 1] += chunk
        event += 1
    np.clip(outdata, -0.98, 0.98, out=outdata)


def _fill_output(state: _State, outdata: np.ndarray, frames: int) -> bool:
    """Render a block, splitting at every loop boundary without silent gaps."""
    outdata.fill(0)
    written = 0
    while written < frames:
        if state.loop_end is not None:
            if state.frame >= state.loop_end:
                state.frame = state.loop_start
            boundary = state.loop_end
        else:
            boundary = state.limit
        available = frames - written
        if boundary is not None:
            available = min(available, max(0, boundary - state.frame))
        if not available:
            break
        _render_segment(state, outdata[written:written + available], state.frame, available)
        state.frame += available
        written += available
        if state.loop_end is not None and state.frame >= state.loop_end:
            state.frame = state.loop_start
    return state.loop_end is None and state.limit is not None and state.frame >= state.limit


def _audible_position(state: _State, elapsed_seconds: float) -> float:
    """Map device playback time to the absolute, possibly looping song clock."""
    elapsed_frames = max(0.0, elapsed_seconds * state.sample_rate)
    position = state.origin_frame + elapsed_frames
    if state.loop_end is not None:
        position = state.loop_start + ((position - state.loop_start) %
                                      (state.loop_end - state.loop_start))
    elif state.limit is not None:
        position = min(position, state.limit)
    return position / state.sample_rate


class PlaybackEngine:
    """Own one output stream. Call start/stop from the application's UI thread."""

    def __init__(self):
        self._stream = None
        self._state = None
        self._running = False
        self._error = None
        self._latency = 0.0
        self._start_stream_time = 0.0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def latency_seconds(self) -> float:
        return self._latency

    @property
    def position_seconds(self) -> float:
        state = self._state
        if state is None:
            return 0.0
        if self._running and self._stream is not None:
            try:
                reference = state.first_dac_time
                if reference is None:
                    reference = self._start_stream_time + self._latency
                state.last_position = _audible_position(
                    state, max(0.0, float(self._stream.time) - reference))
            except Exception:
                pass
        return state.last_position

    def start(self, settings: ClickSettings, audio: AudioData | None = None,
              include_song: bool = False, device=None,
              duration_seconds: float | None = None, start_seconds: float = 0.0,
              loop_start_seconds: float | None = None,
              loop_end_seconds: float | None = None):
        """Start at an absolute song time, optionally repeating [loop start, end).

        ``duration_seconds`` is the absolute track endpoint, including when
        starting partway through it. All boundaries round to audio samples.
        """
        self.stop()
        self._error = None
        self._state = None
        state = _make_state(settings, audio, include_song, duration_seconds,
                            start_seconds, loop_start_seconds, loop_end_seconds)
        self._state = state

        def callback(outdata, frames, time_info, status):
            try:
                if state.first_dac_time is None:
                    dac = float(time_info.outputBufferDacTime)
                    state.first_dac_time = dac if dac > 0 else self._start_stream_time + self._latency
                finished = _fill_output(state, outdata, frames)
                if finished:
                    raise sd.CallbackStop
            except (sd.CallbackStop, sd.CallbackAbort):
                raise
            except Exception as exc:
                outdata.fill(0)
                self._error = f"Audio playback stopped: {exc}"
                self._running = False
                raise sd.CallbackAbort from exc

        def finished():
            if self._state is state:
                if self._running and state.loop_end is None and state.limit is not None and state.frame >= state.limit:
                    state.last_position = state.limit / state.sample_rate
                self._running = False

        stream = None
        try:
            stream = sd.OutputStream(
                samplerate=state.sample_rate, channels=2, dtype="float32",
                blocksize=1024, latency="high", device=device,
                callback=callback, finished_callback=finished,
            )
            self._stream = stream
            self._latency = float(stream.latency)
            self._start_stream_time = float(stream.time)
            self._running = True
            stream.start()
        except Exception as exc:
            self._running = False
            self._stream = None
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            self._error = "Could not open your audio output. Check that speakers or headphones are connected. " + str(exc)
            raise RuntimeError(self._error) from exc

    def stop(self):
        if self._running:
            self.position_seconds
        self._running = False
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

