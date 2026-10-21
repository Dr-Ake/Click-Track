import sys
from pathlib import Path
import wave

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
from audio_core import (AudioData, ClickSettings, analyze_audio, click_waveform,
                        export_click_wav, load_audio, render_clicks)


@pytest.mark.parametrize("bpm", [75, 90, 120, 160])
def test_tempo_and_first_beat(bpm):
    mono = render_clicks(ClickSettings(bpm=bpm, accent=False, offset_seconds=0.3), 20)
    audio = AudioData(np.repeat(mono[:, None], 2, axis=1), 44100, 20.0, "fixture.wav")
    result = analyze_audio(audio)
    assert abs(result.bpm - bpm) <= 0.6, result
    assert abs(result.first_beat_seconds - 0.3) <= 0.025, result
    assert result.confidence in ("High", "Medium"), result


@pytest.mark.parametrize("bpm", [90, 120, 160])
def test_tempo_through_background_and_weak_eighths(bpm):
    sr, seconds = 44100, 24
    times = np.arange(sr * seconds) / sr
    audio = (0.015 * np.sin(2 * np.pi * 220 * times)).astype(np.float32)
    kick_t = np.arange(int(sr * 0.13)) / sr
    kick = (np.sin(2 * np.pi * (65 * kick_t - 30 * kick_t**2)) * np.exp(-kick_t / 0.035)).astype(np.float32)
    rng = np.random.default_rng(7)
    snare = (rng.normal(0, 0.3, len(kick_t)) * np.exp(-kick_t / 0.02)).astype(np.float32)
    hat_t = np.arange(int(sr * 0.025)) / sr
    hat = (rng.normal(0, 0.025, len(hat_t)) * np.exp(-hat_t / 0.005)).astype(np.float32)
    for number in range(int(seconds * bpm / 60 * 2)):
        position = round((0.3 + number * 30 / bpm) * sr)
        if position >= len(audio):
            break
        click = hat if number % 2 else (kick if number % 4 == 0 else snare)
        count = min(len(click), len(audio) - position)
        audio[position:position + count] += click[:count]
    result = analyze_audio(AudioData(np.repeat(audio[:, None], 2, axis=1), sr, seconds, "fixture.wav"))
    assert abs(result.bpm - bpm) <= 1.0, result


def test_silence_rejected():
    with pytest.raises(ValueError, match="silent|pulse"):
        analyze_audio(AudioData(np.zeros((441000, 2), np.float32), 44100, 10, "silent.wav"))


@pytest.mark.parametrize("kind", ["noise", "tone"])
def test_nonrhythmic_audio_rejected(kind):
    if kind == "noise":
        mono = np.random.default_rng(1).normal(0, 0.1, 44100 * 12).astype(np.float32)
    else:
        mono = (0.2 * np.sin(2 * np.pi * 440 * np.arange(44100 * 12) / 44100)).astype(np.float32)
    with pytest.raises(ValueError, match="steady tempo|pulse"):
        analyze_audio(AudioData(np.repeat(mono[:, None], 2, axis=1), 44100, 12, kind))


def test_opposite_polarity_stereo_and_silent_intro():
    mono = render_clicks(ClickSettings(bpm=128, accent=False, offset_seconds=5.3), 24)
    samples = np.column_stack((mono, -mono))
    result = analyze_audio(AudioData(samples, 44100, 24, "opposite-polarity.wav"))
    assert abs(result.bpm - 128) <= 0.6, result
    assert abs(result.first_beat_seconds - 5.3) <= 0.025, result


def test_invalid_import(tmp_path):
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"This is not audio.")
    with pytest.raises(ValueError, match="read as audio"):
        load_audio(broken)


def test_import_mono_resamples_to_stereo(tmp_path):
    sr = 22050
    mono = render_clicks(ClickSettings(bpm=120), 5, sample_rate=sr)
    path = tmp_path / "mono.wav"
    sf.write(path, mono, sr)
    loaded = load_audio(path)
    assert loaded.samples.dtype == np.float32
    assert loaded.samples.shape == (44100 * 5, 2)
    assert loaded.sample_rate == 44100
    assert loaded.duration == 5
    np.testing.assert_array_equal(loaded.samples[:, 0], loaded.samples[:, 1])


def test_chunk_boundaries_identical_to_contiguous_render():
    settings = ClickSettings(bpm=137.3, beats_per_bar=7, subdivision=4,
                             offset_seconds=0.301123, sound="Beep")
    whole = render_clicks(settings, 17.25)
    # Deliberately split through click waveforms and at arbitrary sample positions.
    cuts = [0, 13291, 29999, 352800, 352819, len(whole)]
    chunks = [render_clicks(settings, (end - start) / 44100, start_frame=start)
              for start, end in zip(cuts, cuts[1:])]
    np.testing.assert_array_equal(whole, np.concatenate(chunks))


def test_export_exact_frames_and_pcm(tmp_path):
    settings = ClickSettings(bpm=137.3, beats_per_bar=3, subdivision=2,
                             offset_seconds=0.3, volume=0.75)
    duration = 19.125
    path = tmp_path / "click.wav"
    export_click_wav(path, settings, duration)
    with wave.open(str(path), "rb") as wav:
        assert wav.getnframes() == round(duration * 44100)
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 44100
        actual = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    expected = np.rint(np.clip(render_clicks(settings, duration), -1, 1) * 32767).astype("<i2")
    np.testing.assert_array_equal(actual, expected)
    assert np.all(actual[:round(0.3 * 44100)] == 0)
    assert not list(tmp_path.glob(".clicktrack-*"))


@pytest.mark.parametrize("settings", [ClickSettings(bpm=0), ClickSettings(bpm=float("nan")),
    ClickSettings(subdivision=3), ClickSettings(offset_seconds=-1), ClickSettings(volume=2)])
def test_bad_settings_rejected(settings):
    with pytest.raises(ValueError):
        render_clicks(settings, 2)


def test_cached_waveforms_cannot_be_modified():
    click = click_waveform(44100, True, "Wood")
    assert click is click_waveform(44100, True, "Wood")
    assert np.max(np.abs(click)) == pytest.approx(0.88)
    assert not click.flags.writeable


def test_actual_licensed_music_sample_if_present():
    # Official librosa dataset; Kevin MacLeod, Vibe Ace, CC BY.
    # https://librosa.org/data/audio/Kevin_MacLeod_-_Vibe_Ace.ogg
    # Artist's metadata lists 130 BPM. This fixture is kept only in work/.
    fixture = Path(__file__).resolve().parents[1] / "work" / "validation-vibeace.ogg"
    if not fixture.exists():
        pytest.skip("Optional licensed music validation fixture is not present")
    result = analyze_audio(load_audio(fixture))
    assert abs(result.bpm - 130) < 1.0, result
    assert result.confidence in ("Medium", "High"), result
