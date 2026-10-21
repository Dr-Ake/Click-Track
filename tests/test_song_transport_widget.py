import math
from pathlib import Path
import sys
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'source'))
from song_transport import SongTransport


def pump(seconds=.03):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        QApplication.processEvents()
        time.sleep(.005)


@pytest.fixture(scope='session')
def qtapp():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle('Fusion')
    app.setStyleSheet("QWidget{background:#192130;color:#edf2fb;font:13px 'Segoe UI';} QPushButton{padding:5px;} QDoubleSpinBox{padding:4px;} QLabel#muted{color:#a6b4c9;}")
    return app


@pytest.fixture
def transport(qtapp):
    widget=SongTransport()
    widget.resize(390,180)
    widget.show()
    widget.set_song(20,120,.3,4)
    pump()
    yield widget
    widget.close()
    pump()


def test_programmatic_setters_are_silent_and_pause_position_is_not_snapped(transport):
    seek=QSignalSpy(transport.seekRequested)
    loop=QSignalSpy(transport.loopChanged)
    transport.set_position(3.473)
    assert transport.position_seconds==3.473
    transport.set_loop(True,1.3,8.3)
    transport.set_song(20,90,.412,3)
    assert transport.position_seconds==3.473
    assert transport.loop_start_seconds==pytest.approx(.412+1*60/90)
    assert transport.loop_end_seconds==pytest.approx(.412+12*60/90)
    assert seek.count()==loop.count()==0
    transport.reset()
    assert not transport.isEnabled()
    assert transport.snap_checkbox.isChecked()
    assert seek.count()==loop.count()==0


def test_snap_grid_obeys_offset_and_main_beats(transport):
    assert transport.snap_position(0)==pytest.approx(.3)
    assert transport.snap_position(3.49)==pytest.approx(3.3)
    assert transport.snap_position(3.58)==pytest.approx(3.8)
    assert transport.snap_position(20)==pytest.approx(19.8)
    transport.snap_checkbox.setChecked(False)
    assert transport.snap_position(3.473)==3.473
    assert transport.snap_position(100)<20
    assert transport.snap_position(100,allow_end=True)==20


def test_timeline_drag_seeks_once_on_release(transport):
    spy=QSignalSpy(transport.seekRequested)
    tl=transport.timeline
    start=QPoint(round(tl.time_to_x(2)),23)
    finish=QPoint(round(tl.time_to_x(7.12)),23)
    QTest.mousePress(tl,Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(tl,finish)
    pump()
    assert spy.count()==0
    assert transport.is_interacting
    preview=transport.position_seconds
    transport.set_position(11)
    assert transport.position_seconds==preview
    QTest.mouseRelease(tl,Qt.MouseButton.LeftButton,pos=finish)
    assert spy.count()==1
    assert transport.position_seconds==pytest.approx(7.3)
    assert not transport.is_interacting
    assert spy.at(0)[0]==pytest.approx(7.3)


def test_brackets_drag_snap_and_enable_loop_once(transport):
    spy=QSignalSpy(transport.loopChanged)
    tl=transport.timeline
    # Move A from the full-song start to a nearby beat.
    QTest.mousePress(tl,Qt.MouseButton.LeftButton,pos=QPoint(round(tl.time_to_x(0)),46))
    QTest.mouseMove(tl,QPoint(round(tl.time_to_x(4.57)),46))
    assert spy.count()==0
    QTest.mouseRelease(tl,Qt.MouseButton.LeftButton,pos=QPoint(round(tl.time_to_x(4.57)),46))
    assert spy.count()==1
    assert transport.loop_enabled
    assert transport.loop_start_seconds==pytest.approx(4.8)
    # B becomes the beat nearest the pointer, respecting the offset.
    QTest.mousePress(tl,Qt.MouseButton.LeftButton,pos=QPoint(round(tl.time_to_x(20)),46))
    QTest.mouseRelease(tl,Qt.MouseButton.LeftButton,pos=QPoint(round(tl.time_to_x(10.12)),46))
    assert spy.count()==2
    assert transport.loop_end_seconds==pytest.approx(10.3)
    assert transport.loop_end_seconds-transport.loop_start_seconds>=.5


def test_enabling_default_loop_chooses_full_beats(transport):
    transport.loop_checkbox.setChecked(True)
    assert transport.loop_start_seconds==pytest.approx(.3)
    assert transport.loop_end_seconds==pytest.approx(19.8)
    assert (transport.loop_end_seconds-transport.loop_start_seconds)/.5==pytest.approx(39)
    transport.set_song(.5,120,.3,4)
    assert not transport.loop_checkbox.isEnabled()
    assert not transport.loop_enabled
    transport.set_loop(True,0,.5)
    assert not transport.loop_enabled
    transport.snap_checkbox.setChecked(False)
    assert transport.loop_checkbox.isEnabled()
    transport.loop_checkbox.setChecked(True)
    assert transport.loop_enabled
    assert transport.loop_start_seconds==0
    assert transport.loop_end_seconds==.5


def test_exact_time_edit_and_beat_step_controls(transport):
    spy=QSignalSpy(transport.seekRequested)
    transport.snap_checkbox.setChecked(False)
    transport.position_spin.setValue(3.473)
    assert spy.count()==1
    assert spy.at(0)[0]==3.473
    QTest.mouseClick(transport.next_beat_button,Qt.MouseButton.LeftButton)
    assert transport.position_seconds==pytest.approx(3.8)
    QTest.mouseClick(transport.previous_beat_button,Qt.MouseButton.LeftButton)
    assert transport.position_seconds==pytest.approx(3.3)
    assert spy.count()==3


def test_edited_position_is_not_overwritten_by_playback_timer(transport):
    transport.position_spin.setFocus()
    transport.position_spin.lineEdit().selectAll()
    QTest.keyClicks(transport.position_spin.lineEdit(),'7.127')
    assert transport.is_interacting
    transport.set_position(12)
    assert '7.127' in transport.position_spin.lineEdit().text()
    QTest.keyClick(transport.position_spin,Qt.Key.Key_Return)
    assert transport.position_seconds==pytest.approx(7.3)
    assert not transport.is_interacting


def test_set_a_b_buttons_and_crossing_bounds_are_valid(transport):
    transport.set_position(4.57)
    QTest.mouseClick(transport.set_a_button,Qt.MouseButton.LeftButton)
    assert transport.loop_start_seconds==pytest.approx(4.8)
    transport.set_position(8.22)
    QTest.mouseClick(transport.set_b_button,Qt.MouseButton.LeftButton)
    assert transport.loop_end_seconds==pytest.approx(8.3)
    transport.loop_start_spin.setValue(14)
    assert transport.loop_start_seconds<=transport.loop_end_seconds-.5+1e-8
    assert (transport.loop_start_seconds-.3)/.5==pytest.approx(round((transport.loop_start_seconds-.3)/.5))
    transport.loop_end_spin.setValue(1)
    assert transport.loop_end_seconds>=transport.loop_start_seconds+.5-1e-8


def test_native_compact_render_and_long_song_grid(transport):
    transport.set_song(1800,300,.423,7)
    transport.set_loop(True,33.423,67.423)
    transport.set_position(44.837)
    # Measure compactness in text units so larger system fonts remain readable.
    # About 45 digits gives the original 310px budget at the default font size.
    width_budget = max(310, 45 * transport.fontMetrics().horizontalAdvance('0'))
    height_budget = max(200, 12 * transport.fontMetrics().height())
    transport.resize(width_budget,180)
    pump()
    assert transport.minimumSizeHint().height() <= height_budget
    assert transport.minimumSizeHint().width() <= width_budget
    for control in (transport.time_label, transport.position_spin,
                    transport.snap_checkbox, transport.loop_checkbox,
                    transport.loop_start_spin, transport.loop_end_spin,
                    transport.set_a_button, transport.set_b_button):
        bounds = control.geometry()
        assert bounds.left() >= 0 and bounds.right() < transport.width()
    output=BASE/'work/qa/song-transport.png'
    output.parent.mkdir(parents=True,exist_ok=True)
    assert transport.grab().save(str(output))
    assert transport.time_label.text()=='00:44.837 / 30:00.000'


def test_invalid_values_and_short_saved_loop_are_safely_handled(transport):
    with pytest.raises(ValueError):
        transport.set_song(math.nan,120,0)
    with pytest.raises(ValueError):
        transport.set_song(20,0,0)
    with pytest.raises(ValueError):
        transport.set_position(math.inf)
    transport.set_song(5,120,0)
    transport.set_loop(True,15,25)
    assert transport.loop_start_seconds==0
    assert transport.loop_end_seconds==5
    assert transport.loop_enabled


def test_enabled_loop_resnaps_silently_when_tempo_or_offset_changes(transport):
    transport.set_loop(True,2.3,8.3)
    transport.set_position(3.473)
    seek=QSignalSpy(transport.seekRequested)
    loop=QSignalSpy(transport.loopChanged)
    transport.set_song(20,90,.412,4)
    period=60/90
    assert transport.loop_start_seconds==pytest.approx(.412+3*period)
    assert transport.loop_end_seconds==pytest.approx(.412+12*period)
    assert transport.position_seconds==3.473
    assert seek.count()==loop.count()==0
    # A free-time loop keeps its precise marks when the grid changes.
    transport.snap_checkbox.setChecked(False)
    transport.set_loop(True,2.127,8.729)
    transport.set_song(20,130,.2,4)
    assert transport.loop_start_seconds==2.127
    assert transport.loop_end_seconds==8.729
    assert transport.position_seconds==3.473
    assert seek.count()==loop.count()==0


def test_offset_change_disables_loop_if_a_full_beat_no_longer_fits(transport):
    transport.set_song(10,120,.3,4)
    transport.set_loop(True,2.3,8.3)
    transport.set_position(3.473)
    seek=QSignalSpy(transport.seekRequested)
    loop=QSignalSpy(transport.loopChanged)
    transport.set_song(10,120,9.8,4)
    assert not transport.loop_enabled
    assert not transport.loop_checkbox.isEnabled()
    assert transport.position_seconds==3.473
    assert seek.count()==loop.count()==0
