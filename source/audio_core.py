"""Offline audio decoding, tempo estimation, and sample-accurate click rendering.

Tempo is an estimate of a steady pulse, not a transcription of changing tempo.
Progress callbacks receive ``(percent: int, description: str)``.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np
from scipy import fft, ndimage, signal
import soundfile as sf
from app_paths import project_root

SAMPLE_RATE = 44100
MAX_SONG_SECONDS = 1800
MAX_EXPORT_SECONDS = 7200


@dataclass
class AudioData:
    samples: np.ndarray
    sample_rate: int
    duration: float
    path: str


@dataclass
class AnalysisResult:
    bpm: float
    confidence: str
    first_beat_seconds: float
    message: str


@dataclass
class ClickSettings:
    bpm: float = 120.0
    beats_per_bar: int = 4
    subdivision: int = 1
    accent: bool = True
    volume: float = 0.65
    offset_seconds: float = 0.0
    sound: str = "Wood"


def _progress(callback, percent, message):
    if callback is not None:
        callback(int(percent), str(message))


def _ffmpeg_path():
    roots = [Path(getattr(sys, "_MEIPASS", Path(__file__).parent)),
             Path(__file__).parent, Path(__file__).parent.parent]
    for root in roots:
        for relative in ("ffmpeg.exe", "bin/ffmpeg.exe", "assets/ffmpeg.exe"):
            candidate = root / relative
            if candidate.is_file():
                return str(candidate)
    bundled = project_root() / "app" / "_internal" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    located = shutil.which("ffmpeg")
    if located:
        return located
    raise ValueError("The audio decoder is missing. Keep ClickTrack's support files with the app.")


def load_audio(path, progress=None):
    """Decode a local song to stereo 44.1 kHz float audio, without networking."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("That song could not be found. Choose an existing audio file.")
    _progress(progress, 3, "Opening song…")
    # libsndfile handles WAV/FLAC/OGG and many MP3s without an intermediate file.
    # Probe the frame count first so an oversized import is rejected before allocating.
    samples = None
    try:
        info = sf.info(str(source))
        if info.duration > MAX_SONG_SECONDS:
            raise ValueError("Choose a song shorter than 30 minutes.")
        if info.frames <= 0 or info.samplerate <= 0:
            raise RuntimeError("No audio samples")
        samples, rate = sf.read(str(source), dtype="float32", always_2d=True)
        if samples.shape[1] > 2:
            # General multichannel imports are decoded by FFmpeg's channel mixer.
            samples = None
    except (RuntimeError, sf.LibsndfileError):
        samples = None
    if samples is None:
        _progress(progress, 7, "Decoding song…")
        # A temporary WAV keeps subprocess output out of memory and bounds long files.
        descriptor, temp_name = tempfile.mkstemp(prefix="clicktrack-decode-", suffix=".wav")
        os.close(descriptor)
        try:
            command = [_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin",
                       "-y", "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:a:0", "-vn",
                       "-t", str(MAX_SONG_SECONDS + 0.1), "-ac", "2", "-ar", str(SAMPLE_RATE),
                       "-c:a", "pcm_f32le", "-f", "wav", temp_name]
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                        timeout=240, check=False)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("This song took too long to decode. Try a shorter audio file.") from exc
            if result.returncode != 0:
                raise ValueError("This file could not be read as audio. Try WAV, MP3, FLAC, M4A, or OGG.")
            info = sf.info(temp_name)
            if info.duration > MAX_SONG_SECONDS:
                raise ValueError("Choose a song shorter than 30 minutes.")
            samples, rate = sf.read(temp_name, dtype="float32", always_2d=True)
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    _progress(progress, 17, "Preparing audio…")
    if len(samples) < max(1, int(rate * 0.25)):
        raise ValueError("This audio is too short. Choose at least a few seconds of music.")
    if not np.isfinite(samples).all():
        raise ValueError("This audio contains damaged or invalid samples. Try another file.")
    if samples.shape[1] == 1:
        samples = np.repeat(samples, 2, axis=1)
    if rate != SAMPLE_RATE:
        divisor = math.gcd(int(rate), SAMPLE_RATE)
        samples = signal.resample_poly(samples, SAMPLE_RATE // divisor, int(rate) // divisor,
                                       axis=0).astype(np.float32)
        rate = SAMPLE_RATE
    # Keep floating-point decoder overshoots from clipping the playback mixer.
    peak = float(np.max(np.abs(samples)))
    if peak > 1.0:
        samples /= peak
    samples = np.ascontiguousarray(samples, dtype=np.float32)
    _progress(progress, 22, "Song ready; listening for a pulse…")
    return AudioData(samples, int(rate), len(samples) / float(rate), str(source))


def _mono(samples):
    if samples.ndim == 1:
        return samples
    mean = np.mean(samples, axis=1, dtype=np.float32)
    channel_energy = np.mean(samples.astype(np.float64) ** 2, axis=0)
    if float(np.mean(mean.astype(np.float64) ** 2)) < float(np.max(channel_energy)) * 0.05:
        # Opposite-polarity stereo must not disappear during beat detection.
        return samples[:, int(np.argmax(channel_energy))]
    return mean


def _novelty(samples, sample_rate):
    """Positive spectral flux with an adaptive noise floor; ~86 frames / second."""
    mono = _mono(samples)
    rate = 11025
    divisor = math.gcd(int(sample_rate), rate)
    reduced = signal.resample_poly(mono, rate // divisor, int(sample_rate) // divisor)
    hop, width = 128, 1024
    if len(reduced) < width + hop:
        raise ValueError("Use a longer song so there are enough beats to estimate its tempo.")
    frames = np.lib.stride_tricks.sliding_window_view(reduced, width)[::hop]
    spectrum = np.abs(fft.rfft(frames * signal.windows.hann(width, sym=False), axis=1))
    # Log compression makes softer attacks audible to the detector alongside loud drums.
    spectrum = np.log1p(30.0 * spectrum)
    flux = np.maximum(np.diff(spectrum, axis=0, prepend=spectrum[:1]), 0)
    frequencies = fft.rfftfreq(width, 1 / rate)
    low = np.mean(flux[:, (frequencies >= 35) & (frequencies < 250)], axis=1)
    mid = np.mean(flux[:, (frequencies >= 250) & (frequencies < 2000)], axis=1)
    high = np.mean(flux[:, frequencies >= 2000], axis=1)
    envelope = 0.45 * low + 0.35 * mid + 0.20 * high
    fps = rate / hop
    floor = ndimage.median_filter(envelope, size=43, mode="nearest")
    envelope = np.maximum(envelope - 0.65 * floor, 0)
    envelope = ndimage.gaussian_filter1d(envelope, 0.65)
    if np.max(envelope) > 0:
        envelope /= np.max(envelope)
    times = (np.arange(len(envelope)) * hop + width / 2) / rate
    return envelope, times, fps


def _tempo_scores(envelope, fps, bpms):
    centered = envelope - np.mean(envelope)
    size = fft.next_fast_len(2 * len(centered))
    spectrum = fft.rfft(centered, size)
    acf = fft.irfft(spectrum * np.conjugate(spectrum), size)[:len(centered)]
    acf /= np.arange(len(centered), 0, -1)
    if acf[0] < 1e-10:
        return np.zeros_like(bpms), acf
    acf /= acf[0]
    lags = 60 * fps / bpms
    interpolation = lambda lag: np.interp(lag, np.arange(len(acf)), acf, left=0, right=0)
    a1 = interpolation(lags)
    score = (a1 + 0.5 * interpolation(2 * lags) + 0.25 * interpolation(3 * lags)) / 1.75
    # A slower multiple of an obvious pulse is less useful than that pulse itself.
    faster_pulse = np.maximum.reduce([interpolation(lags / divisor) for divisor in (2, 3, 4)])
    score += 0.12 * (a1 - faster_pulse)
    # Only a gentle human-tempo prior; strong regular beats win outside this region.
    score -= 0.012 * np.log2(bpms / 120.0) ** 2
    return score, acf


def _refine_tempo(envelope, times, fps, initial):
    peaks, properties = signal.find_peaks(envelope, distance=max(1, int(fps * 0.10)),
                                          prominence=max(0.025, float(np.max(envelope)) * 0.10))
    if len(peaks) < 4:
        return initial, 0.0, peaks
    weights = envelope[peaks].astype(np.float64)
    onset_times = times[peaks]
    candidates = np.linspace(initial * 0.975, initial * 1.025, 301)
    phases = 2j * np.pi * np.outer(candidates / 60, onset_times)
    concentration = np.abs(np.exp(phases) @ weights) / np.sum(weights)
    best = int(np.argmax(concentration))
    return float(candidates[best]), float(concentration[best]), peaks


def _refine_onset(samples, sample_rate, approximate):
    start = max(0, int((approximate - 0.09) * sample_rate))
    end = min(len(samples), int((approximate + 0.09) * sample_rate))
    local = samples[start:end]
    if len(local) < 10:
        return max(0.0, approximate)
    mono = _mono(local)
    width = max(3, int(sample_rate * 0.003))
    power = np.convolve(mono.astype(np.float64) ** 2, np.ones(width) / width, mode="same")
    delay = max(1, int(sample_rate * 0.008))
    rise = power - np.r_[np.zeros(delay), power[:-delay]]
    peak_index = int(np.argmax(rise))
    if rise[peak_index] <= 1e-12:
        return max(0.0, approximate)
    begin = max(0, peak_index - int(sample_rate * 0.025))
    crossings = np.flatnonzero(rise[begin:peak_index + 1] > 0.18 * rise[peak_index])
    point = begin + int(crossings[0]) if len(crossings) else peak_index
    return max(0.0, (start + point) / sample_rate - 0.001)


def analyze_audio(audio, progress=None):
    """Estimate a single steady BPM from up to three representative song excerpts.

    Half/double-time ambiguity is intrinsic to beat estimation; the GUI exposes those
    alternatives. The returned first beat is an onset estimate, not a bar/downbeat.
    """
    if audio.duration < 4:
        raise ValueError("Choose at least four seconds of audio to estimate a tempo.")
    if not len(audio.samples) or not np.isfinite(audio.samples).all():
        raise ValueError("There are no valid audio samples to analyze.")
    # Sampling only excerpts keeps analysis work bounded for long imports.
    length = min(45.0, audio.duration)
    starts = [0.0]
    if audio.duration > 65:
        starts.append(max(0.0, (audio.duration - length) / 2))
    if audio.duration > 110:
        starts.append(max(0.0, audio.duration - length - 2.0))
    bpms = np.arange(35.0, 280.001, 0.1)
    analyzed = []
    for index, start in enumerate(starts):
        _progress(progress, 25 + index * 17, "Finding rhythmic attacks…")
        clip = audio.samples[int(start * audio.sample_rate):int((start + length) * audio.sample_rate)]
        if not len(clip) or float(np.sqrt(np.mean(clip.astype(np.float64) ** 2))) < 1e-5:
            continue
        envelope, times, fps = _novelty(clip, audio.sample_rate)
        if float(np.std(envelope)) < 0.01:
            continue
        scores, acf = _tempo_scores(envelope, fps, bpms)
        analyzed.append((scores, envelope, times, fps, start))
    if not analyzed:
        raise ValueError("No clear pulse was found. This audio is silent or has too little rhythmic detail.")
    _progress(progress, 79, "Comparing tempo candidates…")
    combined = np.mean([item[0] for item in analyzed], axis=0)
    best = int(np.argmax(combined))
    local_peaks, _ = signal.find_peaks(combined, distance=30, prominence=0.015)
    candidates = sorted(set([best, *local_peaks]), key=lambda point: combined[point], reverse=True)[:10]
    ranked = []
    for candidate in candidates:
        if combined[candidate] < max(0.04, float(combined[best]) * 0.22):
            continue
        initial = float(bpms[candidate])
        estimates, regularities = [], []
        for scores, envelope, times, fps, start in analyzed:
            estimate, regularity, peaks = _refine_tempo(envelope, times, fps, initial)
            estimates.append(estimate)
            regularities.append(regularity)
        estimate = float(np.median(estimates))
        regularity = float(np.mean(regularities))
        consistency = float(np.std(estimates) / max(1, estimate))
        strength = float(np.interp(estimate, bpms, combined))
        # Autocorrelation often favors a whole two-beat drum pattern. Phase
        # concentration distinguishes the evenly spaced pulse inside that pattern.
        # A broad human-tempo prior resolves weak subdivisions without preventing
        # strong slow or fast rhythms from winning.
        prior = math.exp(-0.65 * math.log2(estimate / 120.0) ** 2)
        quality = max(0, strength) * (0.25 + regularity) * prior / (1 + 20 * consistency)
        ranked.append((quality, estimate, regularity, consistency, strength))
    if not ranked:
        raise ValueError("A steady tempo could not be found. Try clearer drums, or use Tap tempo.")
    _, bpm, regularity, consistency, strength = max(ranked)
    bpm = round(min(300.0, max(20.0, bpm)), 1)
    if strength < 0.10 or regularity < 0.09:
        raise ValueError("A steady tempo could not be found. Try a section with clearer drums, or use Tap tempo.")
    if strength > 0.42 and regularity > 0.40 and consistency < 0.012:
        confidence = "High"
    elif strength > 0.19 and regularity > 0.16 and consistency < 0.025:
        confidence = "Medium"
    else:
        confidence = "Low"
    # Align to a strong beat in the beginning, preserving a genuine silent intro.
    first_scores, envelope, times, fps, start = analyzed[0]
    _, _, peaks = _refine_tempo(envelope, times, fps, bpm)
    if len(peaks):
        weights = envelope[peaks].astype(np.float64)
        period = 60 / bpm
        phases = np.exp(2j * np.pi * (times[peaks] + start) / period)
        phase = (float(np.angle(np.sum(phases * weights))) / (2 * np.pi) * period) % period
        distances = np.abs(((times[peaks] + start - phase + period / 2) % period) - period / 2)
        selected = peaks[(distances < period * 0.17) & (weights > max(0.12, np.max(weights) * 0.20))]
        if not len(selected):
            selected = peaks[weights > max(0.12, np.max(weights) * 0.20)]
        approximate = float(times[int(selected[0])]) + start if len(selected) else float(times[int(peaks[0])]) + start
        offset = _refine_onset(audio.samples, audio.sample_rate, approximate)
    else:
        offset = start
    if confidence == "High":
        message = "A clear, steady pulse was found. Check the first beat by ear."
    elif confidence == "Medium":
        message = "Estimated steady tempo. Try half or double tempo if the pulse feels off."
    else:
        message = "The rhythm is ambiguous or varies. Check by ear, or use Tap tempo."
    _progress(progress, 100, "Tempo estimate ready")
    return AnalysisResult(bpm, confidence, round(offset, 3), message)


def validate_settings(settings):
    if not math.isfinite(float(settings.bpm)) or not 20 <= settings.bpm <= 300:
        raise ValueError("Tempo must be between 20 and 300 BPM.")
    if settings.beats_per_bar not in range(1, 13):
        raise ValueError("Choose between 1 and 12 beats per bar.")
    if settings.subdivision not in (1, 2, 4):
        raise ValueError("Choose quarter, eighth, or sixteenth-note clicks.")
    if not math.isfinite(float(settings.volume)) or not 0 <= settings.volume <= 1:
        raise ValueError("Volume must be between 0 and 100 percent.")
    if not math.isfinite(float(settings.offset_seconds)) or not 0 <= settings.offset_seconds <= MAX_SONG_SECONDS:
        raise ValueError("The first beat must be between 0 and 1800 seconds.")
    if settings.sound not in ("Wood", "Beep"):
        raise ValueError("Choose the Wood or Beep click sound.")


@lru_cache(maxsize=32)
def click_waveform(sample_rate, accent=False, sound="Wood"):
    """Return an immutable mono click. Playback/export apply volume separately."""
    sample_rate = int(sample_rate)
    if not 8000 <= sample_rate <= 192000:
        raise ValueError("Unsupported audio sample rate.")
    if sound not in ("Wood", "Beep"):
        raise ValueError("Choose the Wood or Beep click sound.")
    length = 0.045 if sound == "Wood" else 0.060
    t = np.arange(int(round(length * sample_rate)), dtype=np.float64) / sample_rate
    if sound == "Wood":
        frequency = 2250 if accent else 1450
        value = (np.sin(2 * np.pi * frequency * t) + 0.35 * np.sin(2 * np.pi * frequency * 1.73 * t)
                 + 0.16 * np.random.default_rng(2026 + int(accent)).standard_normal(len(t)))
        value *= (1 - np.exp(-t / 0.0005)) * np.exp(-t / 0.0075)
    else:
        frequency = 1320 if accent else 880
        value = np.sin(2 * np.pi * frequency * t)
        value *= np.minimum(t / 0.0015, 1) * np.exp(-t / 0.018)
        value *= np.minimum((length - t) / 0.005, 1)
    peak = float(np.max(np.abs(value)))
    value = (value / max(peak, 1e-8) * (0.88 if accent else 0.72)).astype(np.float32)
    value.flags.writeable = False
    return value


def render_clicks(settings, duration_seconds, sample_rate=SAMPLE_RATE, start_frame=0):
    """Render a mono chunk from an absolute frame position on the click grid.

    Every event is rounded from its absolute floating-point grid position, so chunks
    share identical timing and never accumulate rounding drift.
    """
    validate_settings(settings)
    if not math.isfinite(float(duration_seconds)) or duration_seconds < 0:
        raise ValueError("Track length must be a positive number of seconds.")
    sample_rate = int(sample_rate)
    start_frame = int(start_frame)
    if start_frame < 0:
        raise ValueError("The playback position cannot be negative.")
    ordinary = click_waveform(sample_rate, False, settings.sound)
    accented = click_waveform(sample_rate, True, settings.sound)
    count = int(round(float(duration_seconds) * sample_rate))
    if count > sample_rate * MAX_EXPORT_SECONDS:
        raise ValueError("Choose a track shorter than two hours.")
    output = np.zeros(count, dtype=np.float32)
    if count == 0 or settings.volume == 0:
        return output
    step = sample_rate * 60 / float(settings.bpm) / settings.subdivision
    origin = float(settings.offset_seconds) * sample_rate
    first = max(0, int(math.floor((start_frame - len(ordinary) - origin) / step)))
    last = max(0, int(math.ceil((start_frame + count - origin) / step)))
    for number in range(first, last + 1):
        position = int(round(origin + number * step)) - start_frame
        waveform = accented if settings.accent and number % (settings.beats_per_bar * settings.subdivision) == 0 else ordinary
        left, right = max(0, position), min(count, position + len(waveform))
        if right > left:
            # Subdivision clicks are quieter than the main pulse.
            level = settings.volume * (0.58 if number % settings.subdivision else 1.0)
            output[left:right] += waveform[left - position:right - position] * level
    return output


def export_click_wav(path, settings, duration_seconds, sample_rate=SAMPLE_RATE, progress=None):
    """Atomically write a standard mono 16-bit WAV using bounded audio chunks."""
    validate_settings(settings)
    if not math.isfinite(float(duration_seconds)) or not 0 < duration_seconds <= MAX_EXPORT_SECONDS:
        raise ValueError("Choose a track length between one second and two hours.")
    total_frames = int(round(float(duration_seconds) * sample_rate))
    if total_frames * 2 > 0xFFFFFF00:
        raise ValueError("This WAV would be too large. Choose a shorter track.")
    destination = Path(path).expanduser()
    if not destination.parent.is_dir():
        raise ValueError("Choose an existing folder for the exported WAV.")
    descriptor, temporary = tempfile.mkstemp(prefix=".clicktrack-", suffix=".wav", dir=str(destination.parent))
    os.close(descriptor)
    try:
        with wave.open(temporary, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(int(sample_rate))
            start, chunk_size = 0, int(sample_rate) * 8
            while start < total_frames:
                frames = min(chunk_size, total_frames - start)
                chunk = render_clicks(settings, frames / sample_rate, sample_rate, start)
                pcm = np.rint(np.clip(chunk, -1, 1) * 32767).astype("<i2")
                writer.writeframesraw(pcm.tobytes())
                start += frames
                _progress(progress, 100 * start / total_frames, "Writing WAV click track…")
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return str(destination)
