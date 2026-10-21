"""Independent absolute-seek and sample-accurate practice-loop checks."""
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE/'source'))
import playback
from audio_core import AudioData, ClickSettings, render_clicks

RATE = 44100


def song(seconds=3):
    t = np.arange(round(seconds * RATE), dtype=np.float64) / RATE
    samples = np.column_stack((.21*np.sin(2*np.pi*91*t),
                               .17*np.cos(2*np.pi*137*t))).astype(np.float32)
    return AudioData(samples, RATE, len(samples)/RATE, 'independent-fixture.wav')


def test_seek_starts_at_exact_song_sample():
    audio = song()
    first = 37913
    settings = ClickSettings(volume=0)
    state = playback._make_state(settings, audio, True, None, first/RATE)
    out = np.empty((8173,2), np.float32)
    assert not playback._fill_output(state, out, len(out))
    np.testing.assert_array_equal(out, audio.samples[first:first+len(out)]*.68)
    assert state.frame == first+len(out)
    assert state.origin_frame == first


@pytest.mark.parametrize('sound',['Wood','Beep'])
def test_seek_keeps_absolute_click_grid_accent_and_offset(sound):
    settings = ClickSettings(bpm=173.9, beats_per_bar=3, subdivision=4,
                             offset_seconds=.31791, volume=.7, sound=sound)
    first = 39214
    count = 61817
    state = playback._make_state(settings, None, False, 10, first/RATE)
    out = np.empty((count,2), np.float32)
    playback._fill_output(state,out,count)
    expected = render_clicks(settings,count/RATE,RATE,start_frame=first)
    np.testing.assert_allclose(out[:,0],expected,atol=1e-7)
    np.testing.assert_array_equal(out[:,0],out[:,1])
    # A seek is a slice of the global metronome, not a fresh accent at frame zero.
    full = render_clicks(settings,(first+count)/RATE,RATE)
    np.testing.assert_array_equal(expected,full[first:first+count])


def test_loop_crosses_many_callback_boundaries_without_gaps():
    audio = song()
    settings = ClickSettings(bpm=137.3, beats_per_bar=3, subdivision=2,
                             offset_seconds=.17319, volume=.57)
    loop_left,loop_right,first=13107,59023,47217
    full_click=render_clicks(settings,audio.duration,RATE)
    full_mix=np.clip(audio.samples*.68+full_click[:,None],-.98,.98)
    state=playback._make_state(settings,audio,True,None,first/RATE,
                               loop_left/RATE,loop_right/RATE)
    pieces=[]
    for count in (1,1024,7103,65537,31,99,104003):
        out=np.full((count,2),np.nan,np.float32)
        assert not playback._fill_output(state,out,count)
        pieces.append(out)
    got=np.concatenate(pieces)
    indices=loop_left+(first-loop_left+np.arange(len(got)))%(loop_right-loop_left)
    np.testing.assert_array_equal(got,full_mix[indices])
    assert state.frame==loop_left+(first-loop_left+len(got))%(loop_right-loop_left)
    assert np.isfinite(got).all()


def test_click_only_loop_uses_supplied_absolute_endpoint():
    settings=ClickSettings(bpm=120,offset_seconds=.3)
    left,right=.3,1.3
    state=playback._make_state(settings,None,False,4,start_seconds=.3,
                               loop_start_seconds=left,loop_end_seconds=right)
    out=np.empty((RATE*3,2),np.float32)
    assert not playback._fill_output(state,out,len(out))
    expected=render_clicks(settings,1,RATE,start_frame=round(left*RATE))
    np.testing.assert_allclose(out[:,0],np.tile(expected,3),atol=1e-7)


def test_start_outside_loop_resets_to_left_edge():
    state=playback._make_state(ClickSettings(),None,False,4,start_seconds=3,
                               loop_start_seconds=1,loop_end_seconds=2)
    assert state.frame==RATE
    assert state.origin_frame==RATE
    assert state.last_position==1


def test_one_sample_loop_is_valid_and_repeats_exact_source_sample():
    audio=song(.1)
    first=173
    state=playback._make_state(ClickSettings(volume=0),audio,True,None,
                               first/RATE,first/RATE,(first+1)/RATE)
    out=np.empty((1024,2),np.float32)
    assert not playback._fill_output(state,out,len(out))
    np.testing.assert_array_equal(out,np.repeat(audio.samples[first:first+1]*.68,len(out),axis=0))


def test_last_song_sample_then_padding_and_exact_endpoint_seek():
    audio=song(.1)
    state=playback._make_state(ClickSettings(volume=0),audio,True,None,
                               (len(audio.samples)-1)/RATE)
    out=np.ones((32,2),np.float32)
    assert playback._fill_output(state,out,len(out))
    np.testing.assert_array_equal(out[0],audio.samples[-1]*.68)
    assert np.count_nonzero(out[1:])==0
    state=playback._make_state(ClickSettings(),audio,True,None,audio.duration)
    assert playback._fill_output(state,out,len(out))
    assert np.count_nonzero(out)==0


@pytest.mark.parametrize('start,left,right,duration',[
    (-.1,None,None,4),(math.nan,None,None,4),(math.inf,None,None,4),
    (4.01,None,None,4),(0,1,None,4),(0,None,2,4),
    (0,-1,2,4),(0,2,1,4),(0,1,1,4),(0,math.nan,2,4),
    (0,1,math.inf,4),(0,1,4.1,4),(0,0,1e-10,4),(0,0,1,None),
])
def test_invalid_seek_and_loop_bounds_are_rejected(start,left,right,duration):
    with pytest.raises(ValueError):
        playback._make_state(ClickSettings(),None,False,duration,start,left,right)


class FakeStream:
    def __init__(self,**kwargs):
        self.kwargs=kwargs
        self.time=100.
        self.latency=.1
        self.closed=False
    def start(self):
        pass
    def abort(self):
        # PortAudio can call finished_callback when manually aborted too.
        self.kwargs['finished_callback']()
    def close(self):
        self.closed=True


def test_absolute_audible_clock_wraps_independently_of_render_ahead(monkeypatch):
    monkeypatch.setattr(playback.sd,'OutputStream',FakeStream)
    engine=playback.PlaybackEngine()
    engine.start(ClickSettings(),duration_seconds=4,start_seconds=1.5,
                 loop_start_seconds=1,loop_end_seconds=2)
    stream=engine._stream
    out=np.empty((1024,2),np.float32)
    stream.kwargs['callback'](out,len(out),SimpleNamespace(outputBufferDacTime=100.1),None)
    assert engine.position_seconds==pytest.approx(1.5)
    stream.time=100.9
    assert engine.position_seconds==pytest.approx(1.3)
    stream.time=101.6
    assert engine.position_seconds==pytest.approx(1.0)
    engine.stop()
    assert engine.position_seconds==pytest.approx(1.0)
    assert stream.closed


def test_manual_stop_preserves_heard_position_before_finite_buffer_drains(monkeypatch):
    monkeypatch.setattr(playback.sd,'OutputStream',FakeStream)
    engine=playback.PlaybackEngine()
    engine.start(ClickSettings(),duration_seconds=.02,start_seconds=.01)
    stream=engine._stream
    out=np.empty((1024,2),np.float32)
    with pytest.raises(playback.sd.CallbackStop):
        stream.kwargs['callback'](out,len(out),SimpleNamespace(outputBufferDacTime=100.1),None)
    stream.time=100.105
    engine.stop()
    assert engine.position_seconds==pytest.approx(.015)


if __name__=='__main__':
    # Brief live output integration check. It does not record any microphone input.
    engine=playback.PlaybackEngine()
    engine.start(ClickSettings(bpm=120,volume=.08,offset_seconds=.3),
                 duration_seconds=4,start_seconds=1.3,
                 loop_start_seconds=.3,loop_end_seconds=1.3)
    positions=[]
    deadline=time.monotonic()+4.25
    while time.monotonic()<deadline and engine.running:
        positions.append(engine.position_seconds)
        time.sleep(.1)
    print({'real_audio_loop_running':engine.running,'error':engine.error,
           'observed_wraps':sum(b<a-.3 for a,b in zip(positions,positions[1:])),
           'minimum_position':min(positions),'maximum_position':max(positions),
           'latency_seconds':engine.latency_seconds})
    assert engine.running and engine.error is None
    assert sum(b<a-.3 for a,b in zip(positions,positions[1:]))>=3
    engine.stop()
