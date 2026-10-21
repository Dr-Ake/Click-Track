"""Shared boundaries between physical audio checks and offline UI checks."""
import pytest
import sounddevice as sd


@pytest.fixture(scope="session")
def require_audio_output():
    """Skip physical playback checks when this machine has no suitable output."""
    try:
        device = sd.query_devices(kind="output")
        if device["max_output_channels"] < 2:
            pytest.skip("Physical playback needs a default stereo audio output device.")
        sd.check_output_settings(channels=2, dtype="float32", samplerate=44100)
    except sd.PortAudioError as exc:
        pytest.skip(f"Physical playback needs an available 44.1 kHz stereo output: {exc}")


class RecordingPlaybackEngine:
    """Record UI playback requests; this does not render or open audio hardware."""
    def __init__(self):
        self.running = False
        self.position_seconds = 0.0
        self.latency_seconds = 0.0
        self.error = None
        self.starts = []

    def start(self, settings, **kwargs):
        self.starts.append((settings, dict(kwargs)))
        self.position_seconds = kwargs.get("start_seconds", 0.0)
        self.running = True

    def stop(self):
        self.running = False


@pytest.fixture
def recording_playback_engine():
    return RecordingPlaybackEngine()
