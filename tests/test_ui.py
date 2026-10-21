import os
import sys
from pathlib import Path
import time
import numpy as np
import soundfile as sf
import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE/'source'))

from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QPushButton
from PySide6.QtCore import QSettings, Qt, QThread, QTimer
from PySide6.QtTest import QTest
import app as appmod


@pytest.fixture(scope='session')
def qtapp():
    a = QApplication.instance() or QApplication([])
    a.setStyle('Fusion')
    a.setStyleSheet(appmod.STYLE)
    a.setQuitOnLastWindowClosed(False)
    return a


@pytest.fixture
def window(qtapp, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, 'QSettings', lambda *args: QSettings(str(tmp_path/'prefs.ini'), QSettings.Format.IniFormat))
    if os.environ.get('CLICKTRACK_DIRECT_QUIT'):
        def direct_run_job(self,job):
            thread=QThread(self)
            job.moveToThread(thread)
            thread.started.connect(job.run)
            job.progress.connect(self._progress)
            job.finished.connect(thread.quit,Qt.ConnectionType.DirectConnection)
            job.finished.connect(job.deleteLater)
            thread.finished.connect(lambda:self._job_done(thread,job))
            self.jobs.append((thread,job))
            self.progress.setRange(0,0)
            self.progress.show()
            thread.start()
        monkeypatch.setattr(appmod.MainWindow,'run_job',direct_run_job)
    w = appmod.MainWindow(library_path=tmp_path/'library.json')
    errors=[]
    monkeypatch.setattr(w,'show_error',lambda title,text:errors.append((title,text)))
    w.test_errors=errors
    w.show()
    QTest.qWait(80)
    yield w
    if w.engine.running:
        w.stop()
    deadline = time.monotonic() + 30
    while w.jobs and time.monotonic() < deadline:
        qtapp.processEvents()
        time.sleep(.01)
    w.close()
    QTest.qWait(30)


def wait_jobs(w, timeout=30):
    deadline = time.monotonic() + timeout
    while w.jobs and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert not w.jobs, 'Worker did not finish in time'


def fixture_song(path, bpm=120, seconds=12):
    sr=44100
    samples=np.zeros((seconds*sr,2), dtype='float32')
    t=np.arange(int(.07*sr))/sr
    wave=(np.sin(2*np.pi*110*t)*np.exp(-45*t)).astype('float32')*.6
    for beat in np.arange(.3,seconds-.1,60/bpm):
        at=round(beat*sr)
        samples[at:at+len(wave),:]+=wave[:,None]
    sf.write(path,samples,sr)
    return path


def test_render_and_bpm_controls(window):
    out=BASE/'work'/'qa'
    out.mkdir(parents=True,exist_ok=True)
    window.grab().save(str(out/'ui_initial.png'))
    window.bpm.setValue(123.4)
    assert window.bpm.value()==123.4
    assert window.tempo_slider.value()==123
    window.tempo_slider.setValue(90)
    assert window.bpm.value()==90
    window.scale_tempo(2)
    assert window.bpm.value()==180
    window.scale_tempo(.5)
    assert window.bpm.value()==90
    QTest.mouseClick(window.plus,Qt.MouseButton.LeftButton)
    assert window.bpm.value()==91
    QTest.mouseClick(window.minus,Qt.MouseButton.LeftButton)
    assert window.bpm.value()==90
    window.meter.setCurrentIndex(6)
    assert window.beat_strip.beats==7
    window.resize(830,670)
    QTest.qWait(80)
    window.grab().save(str(out/'ui_narrow.png'))
    scroll=window.centralWidget()
    assert scroll.horizontalScrollBar().maximum()==0, 'Minimum window width requires horizontal scroll'


def test_tap_tempo(window,monkeypatch):
    taps=iter([1.,1.5,2.,2.5])
    with monkeypatch.context() as patch:
        patch.setattr(appmod.time,'monotonic',lambda: next(taps))
        for _ in range(4):
            window.tap()
    assert window.bpm.value()==120


def test_real_audio_start_stop(require_audio_output, window):
    window.volume.setValue(8)
    window.toggle_play()
    QTest.qWait(700)
    assert window.engine.running
    assert window.engine.error is None
    assert window.engine.position_seconds > .1
    window.bpm.setValue(122)
    assert not window.engine.running
    window.toggle_play()
    QTest.qWait(100)
    window.toggle_play()
    assert not window.engine.running


def test_threaded_import_export(window,tmp_path,monkeypatch,recording_playback_engine):
    song=fixture_song(tmp_path/'fixture.wav')
    window.import_song(str(song))
    assert not window.import_button.isEnabled()
    wait_jobs(window)
    assert window.audio is not None
    assert window.analysis is not None
    assert window.import_button.isEnabled()
    assert window.include_song.isEnabled()
    assert window.song_length.isChecked()
    assert not window.minutes.isEnabled()
    assert window.offset.value()>0
    path=tmp_path/'clicked.wav'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args,**kwargs:(str(path),'WAV audio (*.wav)'))
    QTimer.singleShot(2000, lambda: print('EXPORT DIAG',window.status.text(),window.last_export,window.test_errors,[(t.isRunning(),t.isFinished()) for t,j in window.jobs],flush=True))
    QTest.mouseClick(window.export_button,Qt.MouseButton.LeftButton)
    wait_jobs(window)
    assert path.exists()
    info=sf.info(path)
    assert info.frames==len(window.audio.samples)
    assert window.last_export==str(path)
    window.grab().save(str(BASE/'work'/'qa'/'ui_analyzed.png'))
    # The import/export above is real; only this UI playback request is recorded.
    window.engine=recording_playback_engine
    window.include_song.setChecked(True)
    window.volume.setValue(5)
    window.toggle_play()
    QTest.qWait(500)
    assert window.engine.running
    settings,arguments=window.engine.starts[-1]
    assert arguments['include_song'] is True
    assert arguments['audio'] is window.audio
    assert arguments['duration_seconds']==window.audio.duration
    assert settings.bpm==window.bpm.value()
    window.stop()


def test_close_during_export(window,tmp_path,monkeypatch):
    def delayed_export(path,settings,duration,progress=None):
        time.sleep(.15)
        sf.write(path,np.zeros(4410,dtype='float32'),44100)
    monkeypatch.setattr(appmod,'export_click_wav',delayed_export)
    path=tmp_path/'close.wav'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args,**kwargs:(str(path),'WAV audio (*.wav)'))
    window.export_wav()
    window.close()
    assert window.closing
    wait_jobs(window)
    QTest.qWait(100)
    assert path.exists()
    assert not window.isVisible()


def test_import_failure_does_not_break_controls(window,tmp_path):
    window.import_song(str(tmp_path/'missing.mp3'))
    wait_jobs(window)
    assert window.audio is None
    assert window.import_button.isEnabled()
    assert window.export_button.isEnabled()
    assert not window.include_song.isEnabled()
    assert window.result_label.text()=='Set the tempo by ear'
