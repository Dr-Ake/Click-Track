"""Independent synthetic music acceptance: no ClickTrack waveforms are reused."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
from audio_core import AudioData, analyze_audio

RATE = 22050
INTRO = 1.2


def drum_song(bpm, anti_phase=False):
    """A bass/chord line with syncopated kick, backbeat snare, and eighth hats."""
    rng = np.random.default_rng(4872)
    beat = 60.0 / bpm
    duration = INTRO + 48 * beat + .5
    left = np.zeros(round(duration * RATE), np.float64)
    right = np.zeros_like(left)

    def add(wave, seconds, gain=1., pan=.0):
        start = round(seconds * RATE)
        end = min(len(left), start + len(wave))
        if end > start:
            left[start:end] += wave[:end-start] * gain * (1 - .30 * pan)
            right[start:end] += wave[:end-start] * gain * (1 + .30 * pan)

    kt = np.arange(round(.23 * RATE)) / RATE
    kick = np.sin(2*np.pi*(45*kt + 90*.035*(1-np.exp(-kt/.035)))) * np.exp(-kt/.065)
    kick += .12 * rng.normal(size=len(kt)) * np.exp(-kt/.004)
    st = np.arange(round(.19 * RATE)) / RATE
    noise = rng.normal(size=len(st))
    noise = signal.sosfilt(signal.butter(2, 1300, "highpass", fs=RATE, output="sos"), noise)
    snare = (.7*noise + .35*np.sin(2*np.pi*180*st)) * np.exp(-st/.045)
    ht = np.arange(round(.065 * RATE)) / RATE
    hats = signal.sosfilt(signal.butter(2, 5000, "highpass", fs=RATE, output="sos"), rng.normal(size=len(ht)))
    hats *= np.exp(-ht/.012)
    for n in range(48):
        bar, phase = divmod(n, 4)
        gain = (1., .77, .9, .7)[bar % 4]
        where = INTRO + n * beat
        if phase in (0, 2):
            add(kick, where, .70 * gain)
        if phase in (1, 3):
            add(snare, where, .44 * gain)
        if bar % 2 and phase == 2:
            add(kick, where + .75*beat, .43)
        add(hats, where, .12, -.7)
        add(hats, where + .5*beat, .09, .7)
        # Bass alternates roots/fifths with an independent eight-bar progression.
        frequency = [55., 65.406, 73.416, 49.][(bar // 2) % 4] * (1.5 if phase == 3 else 1)
        t = np.arange(round(.76 * beat * RATE)) / RATE
        bass = (np.sin(2*np.pi*frequency*t) + .22*np.sin(4*np.pi*frequency*t))
        bass *= (1-np.exp(-t/.009)) * np.exp(-t/(.45*beat))
        add(bass, where, .26)
        if phase == 0:
            t = np.arange(round(3.9 * beat * RATE)) / RATE
            pad = sum(np.sin(2*np.pi*frequency*4*ratio*t) for ratio in (1.,1.25,1.5)) / 3
            pad *= (1-np.exp(-t/(.2*beat))) * np.exp(-t/(3*beat))
            add(pad, where, .09, .4)
    stereo = np.column_stack((left, -left if anti_phase else right))
    stereo *= .9 / max(1., np.max(np.abs(stereo)))
    return AudioData(stereo.astype(np.float32), RATE, duration, f"fixture_{bpm}.wav")


@pytest.mark.parametrize("bpm", [72, 95, 128, 174])
def test_realistic_synthetic_song_tempo(bpm):
    result = analyze_audio(drum_song(bpm))
    assert abs(result.bpm - bpm) / bpm < .01, result
    # A beat on the estimated grid should coincide with the known music entry.
    period = 60 / bpm
    distance = abs(((result.first_beat_seconds - INTRO + period/2) % period) - period/2)
    assert distance < .07, result


def test_antiphase_stereo_song_keeps_audible_rhythm():
    result = analyze_audio(drum_song(128, anti_phase=True))
    assert abs(result.bpm - 128) < 1.28, result


def test_silence_has_no_tempo():
    data = AudioData(np.zeros((RATE*10, 2), np.float32), RATE, 10., "silence.wav")
    with pytest.raises(ValueError, match="pulse|rhythm|tempo"):
        analyze_audio(data)


def test_nonrhythmic_noise_cannot_claim_high_confidence():
    rng = np.random.default_rng(912)
    noise = rng.normal(0, .05, (RATE*20, 2)).astype(np.float32)
    data = AudioData(noise, RATE, 20., "noise.wav")
    try:
        result = analyze_audio(data)
    except ValueError:
        return
    assert result.confidence == "Low", result


if __name__ == "__main__":
    for bpm, antiphase in [(72, False), (95, False), (128, False), (174, False), (128, True)]:
        try:
            result = analyze_audio(drum_song(bpm, anti_phase=antiphase))
            print(json.dumps({"expected": bpm, "anti_phase": antiphase, **vars(result)}))
        except Exception as exc:
            print(json.dumps({"expected": bpm, "anti_phase": antiphase, "error": str(exc)}))
