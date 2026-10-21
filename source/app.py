"""ClickTrack: an offline metronome and song tempo finder."""
from __future__ import annotations

import logging
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
from dataclasses import asdict

import numpy as np
import sounddevice as sd
from PySide6.QtCore import Qt, QThread, QObject, Signal, Slot, QTimer, QSettings, QUrl, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QIcon, QPixmap, QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QDoubleSpinBox, QSlider, QComboBox, QCheckBox,
    QSpinBox, QFileDialog, QMessageBox, QProgressBar, QScrollArea, QSizePolicy,
    QListWidget, QListWidgetItem, QAbstractItemView, QInputDialog,
)

from audio_core import ClickSettings, load_audio, analyze_audio, export_click_wav
from playback import PlaybackEngine
from song_library import SongLibrary
from song_transport import SongTransport
from app_paths import data_directory, configure_runtime_temp, project_root, export_directory

APP_NAME = "ClickTrack"
SETTINGS_FORMAT = QSettings.Format.IniFormat


def resource_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


STYLE = """
QWidget { background: #10151f; color: #edf2fb; font-family: 'Segoe UI'; font-size: 13px; }
QFrame#card { background: #192130; border: 1px solid #2c374a; border-radius: 18px; }
QFrame#card QLabel, QFrame#card QWidget#transparent { background: transparent; }
QLabel#brand { font-size: 27px; font-weight: 700; }
QLabel#subtitle { color: #9eacc2; font-size: 13px; }
QLabel#eyebrow { color: #72dfd4; font-size: 11px; font-weight: 700; }
QLabel#title { font-size: 18px; font-weight: 600; }
QLabel#muted { color: #a6b4c9; }
QLabel#result { color: #72dfd4; font-size: 24px; font-weight: 650; }
QPushButton { background: #283449; border: 1px solid #3a4860; border-radius: 9px;
              padding: 10px 14px; font-weight: 600; }
QPushButton:hover { background: #35455e; border-color: #5c7596; }
QPushButton:pressed { background: #445774; }
QPushButton:disabled { color: #67758a; background: #202a3a; border-color: #2a3547; }
QPushButton#primary { background: #6adace; color: #102c2b; border: none; font-size: 16px; padding: 14px; }
QPushButton#primary:hover { background: #8aece2; }
QPushButton#export { background: #8175e3; color: white; border: none; }
QPushButton#export:hover { background: #968aec; }
QPushButton#export:disabled { background: #403d60; color: #aaa4c2; }
QPushButton#small { padding: 7px 12px; }
QDoubleSpinBox, QSpinBox, QComboBox { background: #111925; border: 1px solid #38465c;
    border-radius: 7px; padding: 7px 9px; min-height: 20px; selection-background-color: #437976; }
QDoubleSpinBox#bpm { background: transparent; border: none; font-size: 66px; font-weight: 700;
    color: #f5f8ff; padding: 0px; min-height: 86px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #202a3c; selection-background-color: #3d536c; }
QCheckBox { spacing: 9px; background: transparent; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #596983; border-radius: 4px; background: #101925; }
QCheckBox::indicator:checked { background: #6adace; border-color: #6adace; image: none; }
QSlider { background: transparent; min-height: 22px; }
QSlider::groove:horizontal { height: 5px; background: #344159; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #6adace; border-radius: 2px; }
QSlider::handle:horizontal { background: #e5fffc; border: 3px solid #6adace; width: 13px; margin: -7px 0; border-radius: 9px; }
QProgressBar { border: none; background: #2a364a; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: #72dfd4; border-radius: 3px; }
QScrollArea { border: none; }
QListWidget { background: #111925; border: 1px solid #344159; border-radius: 9px;
              outline: none; padding: 4px; }
QListWidget::item { padding: 8px 7px; border-radius: 6px; margin: 2px 0; }
QListWidget::item:selected { background: #294b53; color: #a5fff4; }
QListWidget::item:hover { background: #29384d; }
QScrollBar:vertical { width: 9px; background: #10151f; }
QScrollBar::handle:vertical { background: #3a485e; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: #f2f6ff; background: #2a374a; border: 1px solid #52627a; padding: 5px; }
"""


def label(text, name=None, wrap=False):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text, callback, name=None):
    w = QPushButton(text)
    if name:
        w.setObjectName(name)
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    w.clicked.connect(callback)
    return w


def card():
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(24, 22, 24, 22)
    lay.setSpacing(13)
    return f, lay


class BeatStrip(QWidget):
    def __init__(self):
        super().__init__()
        self.beats = 4
        self.current = -1
        self.setMinimumHeight(58)
        self.setObjectName("transparent")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        step = min(55.0, (self.width() - 12) / self.beats)
        radius = min(17.0, step * .35)
        left = (self.width() - step * self.beats) / 2
        for i in range(self.beats):
            x = left + step * (i + .5)
            active = i == self.current
            p.setBrush(QColor("#6adace" if active else "#29364b"))
            p.setPen(QPen(QColor("#9cf6ed" if active else "#465670"), 2 if i == 0 else 1))
            p.drawEllipse(int(x-radius), 6, int(radius*2), int(radius*2))
            p.setPen(QColor("#103330" if active else "#afbdd2"))
            p.drawText(int(x-radius), 6, int(radius*2), int(radius*2), Qt.AlignmentFlag.AlignCenter, str(i+1))


class Job(QObject):
    progress = Signal(int, str)
    song_ready = Signal(object)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, kind, *args):
        super().__init__()
        self.kind, self.args = kind, args

    def report(self, *args):
        # Core reports integer percentages, from 0 to 100.
        if args and isinstance(args[0], (int, float)):
            value = args[0]
            self.progress.emit(int(value), str(args[1]) if len(args) > 1 else "Working…")
        elif args:
            self.progress.emit(0, str(args[0]))

    @Slot()
    def run(self):
        temporary = None
        try:
            if self.kind in ("song", "load"):
                audio = load_audio(self.args[0], progress=self.report)
                if self.kind == "song":
                    self.song_ready.emit(audio)
                    self.result.emit(analyze_audio(audio, progress=self.report))
                else:
                    self.result.emit(audio)
            else:
                destination, settings, duration = self.args
                temporary = str(Path(destination).with_name("." + Path(destination).stem + "." + uuid.uuid4().hex + ".tmp.wav"))
                export_click_wav(temporary, settings, duration, progress=self.report)
                os.replace(temporary, destination)
                temporary = None
                self.result.emit(destination)
        except Exception as exc:
            logging.exception("%s failed", self.kind)
            self.error.emit(str(exc) or type(exc).__name__)
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self, library_path=None):
        super().__init__()
        self.setWindowTitle("ClickTrack — Metronome & Song Tempo")
        self.setWindowIcon(QIcon(str(resource_path("clicktrack.ico"))))
        self.setMinimumSize(1100, 670)
        screen = QApplication.primaryScreen()
        self.resize(1280, min(960, max(670, screen.availableGeometry().height()-40)) if screen else 850)
        self.setAcceptDrops(True)
        self.engine = PlaybackEngine()
        self.audio = None
        self.analysis = None
        self.jobs = []
        self.closing = False
        self.taps = []
        data_directory().mkdir(parents=True, exist_ok=True)
        self.preferences = QSettings(str(data_directory() / "settings.ini"), SETTINGS_FORMAT)
        self.library = SongLibrary(library_path)
        self._library_last_error = None
        self.active_song_id = None
        self._applying_song = True
        self._tempo_edited = False
        self._selection_revision = 0
        self._import_queue = []
        self._import_context = None
        self._imported_audio = None
        self._pending_library_load = None
        self._awaiting_song_start = None
        self._selected_position = 0.0
        self._playback_active = False
        self._transport_grid = None
        self._song_cursors = {}
        self._practice_regions = self._read_practice_regions()
        self.library_save_timer = QTimer(self)
        self.library_save_timer.setSingleShot(True)
        self.library_save_timer.setInterval(350)
        self.library_save_timer.timeout.connect(self.save_active_song)
        self.last_export = None
        self._build()
        self._restore()
        self._applying_song = False
        self.refresh_library()
        if self.library.error:
            self.library_note.setText(self.library.error)
            self.status.setText("The song library could not be opened. Your original library file has been preserved.")
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.space = QShortcut(QKeySequence("Space"), self)
        self.space.activated.connect(self.toggle_play)
        self.tap_shortcut = QShortcut(QKeySequence("T"), self)
        self.tap_shortcut.activated.connect(self.tap)

    def _build(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        main = QVBoxLayout(content)
        main.setContentsMargins(30, 25, 30, 20)
        main.setSpacing(18)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(resource_path("clicktrack.png"))).scaled(47,47,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        header.addWidget(logo)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(label("ClickTrack", "brand"))
        words.addWidget(label("Find the tempo. Keep the beat.", "subtitle"))
        header.addLayout(words)
        header.addStretch()
        header.addWidget(label("LOCAL AUDIO  •  NO UPLOADS", "eyebrow"))
        main.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        library_card, library_layout = card()
        library_card.setMinimumWidth(235)
        library_card.setMaximumWidth(280)
        library_layout.setContentsMargins(16, 22, 16, 20)
        library_layout.addWidget(label("YOUR SONGLIST", "eyebrow"))
        library_layout.addWidget(label("Song library", "title"))
        self.library_add_button = button("+   Add songs…", self.choose_song)
        library_layout.addWidget(self.library_add_button)
        self.library_list = QListWidget()
        self.library_list.setAccessibleName("Song library")
        self.library_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.library_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.library_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.library_list.setDragDropOverwriteMode(False)
        self.library_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.library_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.library_list.setMinimumHeight(240)
        self.library_list.currentItemChanged.connect(self._library_selection_changed)
        self.library_list.model().rowsMoved.connect(self._library_order_changed)
        library_layout.addWidget(self.library_list, 1)
        self.library_note = label("Add songs, then click one to load its click track. Drag to arrange. Changes save automatically.", "muted", True)
        library_layout.addWidget(self.library_note)
        move_row = QHBoxLayout()
        self.library_up = button("↑  Up", lambda: self.move_library_song(-1), "small")
        self.library_down = button("↓  Down", lambda: self.move_library_song(1), "small")
        move_row.addWidget(self.library_up)
        move_row.addWidget(self.library_down)
        library_layout.addLayout(move_row)
        edit_row = QHBoxLayout()
        self.library_rename = button("Rename", self.rename_library_song, "small")
        self.library_remove = button("Remove", self.remove_library_song, "small")
        self.library_remove.setToolTip("Remove this entry from the library. The original song file is kept.")
        edit_row.addWidget(self.library_rename)
        edit_row.addWidget(self.library_remove)
        library_layout.addLayout(edit_row)
        self.library_locate = button("Locate audio file…", self.locate_library_song, "small")
        library_layout.addWidget(self.library_locate)
        columns.addWidget(library_card)
        left, ll = card()
        ll.addWidget(label("YOUR CLICK TRACK", "eyebrow"))
        bpm_row = QHBoxLayout()
        self.minus = button("−", lambda: self.bpm.setValue(self.bpm.value()-1), "small")
        self.bpm = QDoubleSpinBox()
        self.bpm.setObjectName("bpm")
        self.bpm.setRange(20, 300)
        self.bpm.setDecimals(1)
        self.bpm.setSingleStep(1)
        # Qt 6.12's styled NoButtons mode collapses the editor to one pixel.
        self.bpm.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.bpm.setStyleSheet("QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; height: 0px; }")
        self.bpm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bpm.setKeyboardTracking(False)
        self.bpm.setToolTip("Type any tempo from 20 to 300 BPM.")
        self.plus = button("+", lambda: self.bpm.setValue(self.bpm.value()+1), "small")
        bpm_row.addWidget(self.minus)
        bpm_row.addWidget(self.bpm, 1)
        bpm_row.addWidget(self.plus)
        ll.addLayout(bpm_row)
        bpm_label = label("BEATS PER MINUTE", "muted")
        bpm_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(bpm_label)
        self.tempo_slider = QSlider(Qt.Orientation.Horizontal)
        self.tempo_slider.setRange(20, 300)
        self.tempo_slider.valueChanged.connect(self._slider_changed)
        self.bpm.valueChanged.connect(self._bpm_changed)
        ll.addWidget(self.tempo_slider)
        tools = QHBoxLayout()
        tools.addWidget(button("÷ 2", lambda: self.scale_tempo(.5), "small"))
        self.tap_button = button("Tap tempo  ·  T", self.tap, "small")
        tools.addWidget(self.tap_button, 1)
        tools.addWidget(button("× 2", lambda: self.scale_tempo(2), "small"))
        ll.addLayout(tools)
        self.beat_strip = BeatStrip()
        ll.addWidget(self.beat_strip)
        self.play_button = button("▶   Start click", self.toggle_play, "primary")
        ll.addWidget(self.play_button)
        self.transport = SongTransport()
        self.transport.seekRequested.connect(self._seek_position)
        self.transport.loopChanged.connect(self._loop_changed)
        self.transport.snap_checkbox.toggled.connect(self._save_snap_preference)
        self.transport.snap_checkbox.setChecked(str(self.preferences.value("snap_to_beat", "true")).lower() == "true")
        self.transport.set_enabled(False)
        ll.addWidget(self.transport)
        self.clock = label("Space to play / pause", "muted")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(self.clock)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        self.meter = QComboBox()
        for n in range(1, 13):
            self.meter.addItem(f"{n} beats", n)
        self.meter.setCurrentIndex(3)
        self.subdivision = QComboBox()
        for text, value in [("Quarter notes", 1), ("Eighth notes", 2), ("Sixteenth notes", 4)]:
            self.subdivision.addItem(text, value)
        grid.addWidget(label("Beats in a bar", "muted"), 0, 0)
        grid.addWidget(label("Click every", "muted"), 0, 1)
        grid.addWidget(self.meter, 1, 0)
        grid.addWidget(self.subdivision, 1, 1)
        ll.addLayout(grid)
        options = QHBoxLayout()
        self.accent = QCheckBox("Accent first beat")
        self.accent.setChecked(True)
        options.addWidget(self.accent)
        options.addStretch()
        self.sound = QComboBox()
        self.sound.addItems(["Wood", "Beep"])
        self.sound.setToolTip("Click sound")
        options.addWidget(self.sound)
        ll.addLayout(options)
        volume = QHBoxLayout()
        volume.addWidget(label("Click volume", "muted"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(65)
        volume.addWidget(self.volume, 1)
        self.volume_label = label("65%", "muted")
        self.volume_label.setMinimumWidth(36)
        volume.addWidget(self.volume_label)
        self.volume.valueChanged.connect(lambda x: self.volume_label.setText(f"{x}%"))
        ll.addLayout(volume)
        ll.addStretch()
        columns.addWidget(left, 1)

        right = QVBoxLayout()
        right.setSpacing(18)
        song, sl = card()
        sl.addWidget(label("FIND A SONG’S TEMPO", "eyebrow"))
        sl.addWidget(label("Drop a song here", "title"))
        sl.addWidget(label("MP3, WAV, FLAC, M4A, OGG & more\nChoose a file on your computer, up to 30 minutes.", "muted", True))
        self.import_button = button("+   Choose song", self.choose_song)
        sl.addWidget(self.import_button)
        self.song_name = label("No song selected", "muted", True)
        sl.addWidget(self.song_name)
        self.result_label = label("Ready when you are", "result")
        sl.addWidget(self.result_label)
        self.analysis_note = label("A steady beat gives the best estimate. You can always adjust the BPM by ear.", "muted", True)
        sl.addWidget(self.analysis_note)
        self.include_song = QCheckBox("Play song with the click")
        self.include_song.setEnabled(False)
        sl.addWidget(self.include_song)
        offset_row = QHBoxLayout()
        offset_row.addWidget(label("First click at", "muted"))
        self.offset = QDoubleSpinBox()
        self.offset.setRange(0, 1800)
        self.offset.setDecimals(3)
        self.offset.setSingleStep(.01)
        self.offset.setSuffix(" s")
        self.offset.setEnabled(False)
        self.offset.setToolTip("Estimated first beat in the song. Adjust if the clicks are early or late. Used for song playback and song-length export.")
        offset_row.addWidget(self.offset, 1)
        sl.addLayout(offset_row)
        right.addWidget(song, 3)

        export, el = card()
        el.addWidget(label("TAKE THE BEAT WITH YOU", "eyebrow"))
        el.addWidget(label("Export a click-only WAV", "title"))
        self.song_length = QCheckBox("Match the imported song’s length & offset")
        self.song_length.setEnabled(False)
        el.addWidget(self.song_length)
        duration = QHBoxLayout()
        duration.addWidget(label("Length", "muted"))
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 120)
        self.minutes.setValue(3)
        self.minutes.setSuffix(" min")
        self.seconds = QSpinBox()
        self.seconds.setRange(0, 59)
        self.seconds.setSuffix(" sec")
        self.minutes.valueChanged.connect(lambda value: self.seconds.setMaximum(0 if value == 120 else 59))
        duration.addWidget(self.minutes, 1)
        duration.addWidget(self.seconds, 1)
        el.addLayout(duration)
        self.song_length.toggled.connect(lambda checked: [self.minutes.setEnabled(not checked), self.seconds.setEnabled(not checked)])
        self.song_length.toggled.connect(self._schedule_song_save)
        self.export_button = button("↓   Save WAV…", self.export_wav, "export")
        el.addWidget(self.export_button)
        self.show_export = button("Open saved file’s folder", self.reveal_export, "small")
        self.show_export.hide()
        el.addWidget(self.show_export)
        right.addWidget(export, 2)
        columns.addLayout(right, 1)
        main.addLayout(columns, 1)

        output = QHBoxLayout()
        output.addWidget(label("Audio output", "muted"))
        self.device = QComboBox()
        self.device.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.refresh_devices()
        output.addWidget(self.device, 1)
        output.addWidget(button("Refresh", self.refresh_devices, "small"))
        main.addLayout(output)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        main.addWidget(self.progress)
        self.status = label("Ready. Set a tempo or choose a song to begin.", "muted", True)
        main.addWidget(self.status)
        for w in [self.meter, self.subdivision, self.sound, self.device]:
            w.currentIndexChanged.connect(self._settings_changed)
        for w in [self.accent, self.include_song]:
            w.toggled.connect(self._settings_changed)
        self.offset.valueChanged.connect(self._settings_changed)
        self.volume.valueChanged.connect(self._settings_changed)

    def _restore(self):
        p = self.preferences
        self.bpm.setValue(float(p.value("bpm", 120)))
        self.volume.setValue(int(p.value("volume", 65)))
        self.meter.setCurrentIndex(max(0, min(11, int(p.value("meter", 3)))))
        self.subdivision.setCurrentIndex(max(0, min(2, int(p.value("subdivision", 0)))))
        self.sound.setCurrentIndex(max(0, min(1, int(p.value("sound", 0)))))
        self.accent.setChecked(str(p.value("accent", "true")).lower() == "true")

    def refresh_devices(self):
        current = self.device.currentData() if hasattr(self, "device") else None
        self.device.clear()
        self.device.addItem("System default output", None)
        try:
            devices = sd.query_devices()
            apis = sd.query_hostapis()
            for i, d in enumerate(devices):
                if d["max_output_channels"] >= 2 and "WASAPI" in apis[d["hostapi"]]["name"]:
                    self.device.addItem(d["name"], i)
            ix = self.device.findData(current)
            if ix >= 0:
                self.device.setCurrentIndex(ix)
        except Exception:
            logging.exception("Cannot enumerate audio devices")

    def _slider_changed(self, value):
        self.bpm.setValue(value)

    def _bpm_changed(self, value):
        self.tempo_slider.blockSignals(True)
        self.tempo_slider.setValue(round(value))
        self.tempo_slider.blockSignals(False)
        if not self._applying_song and self.active_song_id:
            self._tempo_edited = True
        self._settings_changed()

    def _settings_changed(self, *_):
        self.beat_strip.beats = self.meter.currentData()
        self.beat_strip.update()
        if self.engine.running or self._awaiting_song_start is not None:
            self.stop()
            self.status.setText("Settings updated. Press Play to resume from the selected position.")
        if not self._applying_song and hasattr(self, "transport"):
            self._sync_transport()
        self._schedule_song_save()

    def _read_practice_regions(self):
        try:
            regions = json.loads(str(self.preferences.value("practice_regions", "{}")))
            if not isinstance(regions, dict):
                return {}
            valid = {}
            for song_id, item in regions.items():
                if not isinstance(song_id, str) or not isinstance(item, dict):
                    continue
                start, end = float(item["start"]), float(item["end"])
                if math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= 1800:
                    valid[song_id] = dict(start=start, end=end, enabled=bool(item.get("enabled", False)))
            return valid
        except (ValueError, TypeError, KeyError):
            return {}

    def _save_snap_preference(self, checked):
        if not self._applying_song:
            self.preferences.setValue("snap_to_beat", bool(checked))

    def _remember_transport(self, persist=False):
        if self.active_song_id and self._song_duration() > 0:
            self._song_cursors[self.active_song_id] = self._selected_position
            if persist:
                start = self.transport.loop_start_seconds
                end = self.transport.loop_end_seconds
                if end > start:
                    self._practice_regions[self.active_song_id] = dict(
                        enabled=self.transport.loop_enabled, start=start, end=end)
                    self.preferences.setValue("practice_regions", json.dumps(self._practice_regions))
                    self.preferences.sync()

    def _sync_transport(self):
        duration = self._song_duration()
        grid = (self.bpm.value(), self.offset.value())
        grid_changed = self._transport_grid is not None and self._transport_grid != grid
        was_looping = self.transport.loop_enabled
        self.transport.set_song(duration, self.bpm.value(), self.offset.value(), self.meter.currentData())
        self._transport_grid = grid
        self.transport.set_enabled(duration > 0)
        self._selected_position = max(0.0, min(self._selected_position, duration)) if duration else 0.0
        self.transport.set_position(self._selected_position)
        if grid_changed and was_looping and not self._applying_song:
            self._remember_transport(persist=True)

    def _restore_transport(self):
        self.transport.reset()
        self._selected_position = 0.0
        self._sync_transport()
        region = self._practice_regions.get(self.active_song_id)
        if region:
            self.transport.set_loop(region["enabled"], region["start"], region["end"])
        self._selected_position = self._song_cursors.get(self.active_song_id,
            self.transport.loop_start_seconds if self.transport.loop_enabled else 0.0)
        if self.transport.loop_enabled and not self.transport.loop_start_seconds <= self._selected_position < self.transport.loop_end_seconds:
            self._selected_position = self.transport.loop_start_seconds
        self.transport.set_position(self._selected_position)
        self._update_play_label()

    def _update_play_label(self):
        if self.engine.running:
            self.play_button.setText("❚❚   Pause")
        elif self._awaiting_song_start is not None:
            self.play_button.setText("…   Cancel start")
        else:
            self.play_button.setText("▶   Resume click" if self._selected_position > 0 else "▶   Start click")

    def _seek_position(self, seconds):
        duration = self._song_duration()
        if duration <= 0 or self.closing:
            return
        was_playing = self.engine.running
        if was_playing:
            self.engine.stop()
            self._playback_active = False
        self._selected_position = max(0.0, min(float(seconds), max(0.0, duration - 1/44100)))
        self.transport.set_position(self._selected_position)
        self._remember_transport()
        if was_playing:
            self._begin_playback()
        else:
            self._update_play_label()
            self.status.setText(f"Ready at {self._selected_position:.3f} seconds. Press Play.")

    def _loop_changed(self, enabled, start, end):
        if self._applying_song or self.closing:
            return
        # The widget's user controls already apply the chosen beat snapping.
        self.transport.set_loop(enabled, start, end)
        enabled = self.transport.loop_enabled
        start, end = self.transport.loop_start_seconds, self.transport.loop_end_seconds
        was_playing = self.engine.running
        if was_playing:
            self._selected_position = self.engine.position_seconds
            self.engine.stop()
            self._playback_active = False
        if enabled and not start <= self._selected_position < end:
            self._selected_position = start
        self.transport.set_position(self._selected_position)
        self._remember_transport(persist=True)
        if was_playing:
            self._begin_playback()
        else:
            self._update_play_label()
            self.status.setText(f"Practice loop: {start:.3f}–{end:.3f} seconds. Press Play." if enabled else "Loop off. Playback will continue to the end of the song.")

    def _schedule_song_save(self, *_):
        if not self._applying_song and self.active_song_id:
            self.library_save_timer.start()

    def refresh_library(self):
        self.library_list.blockSignals(True)
        self.library_list.clear()
        for index, entry in enumerate(self.library.songs):
            item = QListWidgetItem()
            self._set_library_item_text(item, entry, index)
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            item.setSizeHint(QSize(0, 64))
            self.library_list.addItem(item)
            if entry["id"] == self.active_song_id:
                self.library_list.setCurrentItem(item)
        if self.active_song_id is None:
            self.library_list.setCurrentRow(-1)
        self.library_list.blockSignals(False)
        selected = self.active_song_id is not None
        writable = not self.library.error
        for widget in [self.library_rename, self.library_remove, self.library_up, self.library_down, self.library_locate]:
            widget.setEnabled(selected and writable)
        if self.library.error or self._library_last_error:
            self.library_note.setText(self.library.error or self._library_last_error)
        else:
            self.library_note.setText("Click a song to load its click track. Drag to arrange. Changes save automatically." if self.library.songs else
                "Add songs to build your library. Saved BPM and click settings stay with each song.")

    def _set_library_item_text(self, item, entry, index):
        tempo = f"{entry['bpm']:g} BPM" if entry["analyzed"] else "Set BPM by ear"
        missing = "  ·  Audio moved" if not Path(entry["path"]).is_file() else ""
        item.setText(f"{index + 1:02}   {entry['title']}\n{tempo}  ·  {entry['beats_per_bar']} beats{missing}")
        item.setToolTip(f"{entry['title']}\n{entry['path']}\n{tempo}. Click to load saved settings.")

    def _library_selection_changed(self, current, previous):
        if current:
            self.load_library_song(current.data(Qt.ItemDataRole.UserRole))

    def _library_order_changed(self, *_):
        ids = [self.library_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.library_list.count())]
        try:
            self.library.reorder(ids)
            self._library_last_error = None
            for i, song_id in enumerate(ids):
                self._set_library_item_text(self.library_list.item(i), self.library.get(song_id), i)
        except (OSError, ValueError, KeyError) as exc:
            self._library_error(exc)
            self.refresh_library()

    def move_library_song(self, direction):
        row = self.library_list.currentRow()
        target = row + direction
        if row < 0 or not 0 <= target < self.library_list.count():
            return
        self.library_list.blockSignals(True)
        item = self.library_list.takeItem(row)
        self.library_list.insertItem(target, item)
        self.library_list.setCurrentItem(item)
        self.library_list.blockSignals(False)
        self._library_order_changed()

    def _library_error(self, exc):
        logging.error("Library could not be saved: %s", exc)
        text = f"Library could not be saved: {exc}"
        self._library_last_error = text
        self.library_note.setText(text)
        self.status.setText(text)

    def save_active_song(self):
        self.library_save_timer.stop()
        if self._applying_song or not self.active_song_id:
            return True
        self._remember_transport()
        entry = self.library.get(self.active_song_id)
        if not entry:
            return True
        fields = asdict(self.settings(True))
        fields.update(include_song=self.include_song.isChecked() if self.include_song.isEnabled() else entry["include_song"],
                      match_song_length=self.song_length.isChecked())
        if self.audio is not None:
            fields["duration_seconds"] = self.audio.duration
        if self._tempo_edited:
            fields.update(analyzed=True, confidence="Manual")
        if all(entry[key] == value for key, value in fields.items()):
            return True
        try:
            self.library.update(self.active_song_id, **fields)
            self._library_last_error = None
            self._tempo_edited = False
            self.refresh_library()
            return True
        except (OSError, ValueError, KeyError) as exc:
            self._library_error(exc)
            return False

    def load_library_song(self, song_id):
        if self.closing:
            return
        if song_id == self.active_song_id:
            return
        if not self.save_active_song():
            self.refresh_library()
            return
        entry = self.library.get(song_id)
        if not entry:
            return
        self._selection_revision += 1
        self._pending_library_load = None
        self._apply_library_song(entry)

    def _apply_library_song(self, entry, audio=None):
        self.stop()
        self.library_save_timer.stop()
        self._applying_song = True
        self.active_song_id = entry.get("id")
        self.audio = audio
        self.analysis = None
        self.taps = []
        self._tempo_edited = False
        self.bpm.setValue(entry["bpm"])
        self.meter.setCurrentIndex(self.meter.findData(entry["beats_per_bar"]))
        self.subdivision.setCurrentIndex(self.subdivision.findData(entry["subdivision"]))
        self.sound.setCurrentText(entry["sound"])
        self.accent.setChecked(entry["accent"])
        self.volume.setValue(round(entry["volume"] * 100))
        self.offset.setValue(entry["offset_seconds"])
        self.offset.setEnabled(True)
        available = Path(entry["path"]).is_file()
        self.include_song.setEnabled(available)
        # Retain a saved song-playback preference even when its file is missing.
        self.include_song.setChecked(entry["include_song"] if available else False)
        has_duration = entry["duration_seconds"] > 0
        self.song_length.setEnabled(has_duration)
        self.song_length.setChecked(entry["match_song_length"] and has_duration)
        seconds = int(entry["duration_seconds"])
        self.song_name.setText(f"{entry['title']}  ·  {seconds//60}:{seconds%60:02}")
        self.song_name.setToolTip(entry["path"])
        self.result_label.setText(f"{entry['bpm']:.1f} BPM" if entry["analyzed"] else "Choose this song’s BPM")
        self.analysis_note.setText("Saved click settings loaded. Changes save automatically." if available else
            "Audio file missing. The saved click still works. Use Locate audio file to reconnect the song.")
        self._applying_song = False
        self._restore_transport()
        self.refresh_library()
        self.status.setText(f"Ready: {entry['title']} · {entry['bpm']:g} BPM. Press Start.")

    def rename_library_song(self):
        entry = self.library.get(self.active_song_id)
        if not entry:
            return
        title, accepted = QInputDialog.getText(self, "Rename song", "Song name:", text=entry["title"])
        if accepted and title.strip():
            try:
                self.library.update(entry["id"], title=title.strip())
                self._library_last_error = None
                self.song_name.setText(title.strip())
                self.refresh_library()
            except (OSError, ValueError, KeyError) as exc:
                self._library_error(exc)

    def remove_library_song(self):
        entry = self.library.get(self.active_song_id)
        if not entry:
            return
        try:
            self.library.remove(entry["id"])
            self._library_last_error = None
        except (OSError, ValueError, KeyError) as exc:
            self._library_error(exc)
            return
        self.stop()
        self.library_save_timer.stop()
        self._selection_revision += 1
        self.active_song_id = None
        self._pending_library_load = None
        self._practice_regions.pop(entry["id"], None)
        self._song_cursors.pop(entry["id"], None)
        self.preferences.setValue("practice_regions", json.dumps(self._practice_regions))
        # Keep the tempo and rhythm usable as an unsaved metronome.
        self._applying_song = True
        self.audio = self.analysis = None
        self.include_song.setChecked(False)
        self.include_song.setEnabled(False)
        self.song_length.setChecked(False)
        self.song_length.setEnabled(False)
        self.offset.setValue(0)
        self.offset.setEnabled(False)
        self._applying_song = False
        self._restore_transport()
        self.song_name.setText("No song selected")
        self.result_label.setText(f"{self.bpm.value():.1f} BPM")
        self.analysis_note.setText("Your tempo and rhythm are ready. Select another library song to load its saved setup.")
        self.refresh_library()
        self.status.setText(f"Removed {entry['title']} from the library. The song file was kept.")

    def locate_library_song(self):
        entry = self.library.get(self.active_song_id)
        if not entry or not self.save_active_song():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Locate this song’s audio file", str(Path(entry["path"]).parent),
            "Audio files (*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus *.wma *.aiff *.aif);;All files (*)")
        if path:
            try:
                updated = self.library.update(entry["id"], path=path)
                self._library_last_error = None
                self._selection_revision += 1
                self._apply_library_song(updated)
            except (OSError, ValueError, KeyError) as exc:
                self._library_error(exc)

    def _song_duration(self):
        entry = self.library.get(self.active_song_id)
        return self.audio.duration if self.audio is not None else entry["duration_seconds"] if entry else 0

    def _request_song_audio(self, autoplay=False):
        entry = self.library.get(self.active_song_id)
        if not entry or not Path(entry["path"]).is_file():
            self.status.setText("The audio file is missing. Reconnect it with Locate audio file, or turn off song playback.")
            return
        token = self._selection_revision
        if autoplay:
            self._awaiting_song_start = token
            self.play_button.setText("…   Cancel start")
        self._pending_library_load = (entry["id"], token, autoplay)
        self._start_pending_audio_load()

    def _start_pending_audio_load(self):
        if not self._pending_library_load or self.closing or any(j.kind == "load" for _, j in self.jobs):
            return
        song_id, token, autoplay = self._pending_library_load
        self._pending_library_load = None
        if song_id != self.active_song_id or token != self._selection_revision:
            return
        entry = self.library.get(song_id)
        if not entry:
            return
        job = Job("load", entry["path"])
        job.result.connect(lambda audio: self._library_audio_ready(song_id, token, autoplay, audio))
        job.error.connect(lambda text: self._library_audio_error(song_id, token, text))
        self.status.setText(f"Opening {entry['title']}…")
        self.run_job(job)

    def _library_audio_ready(self, song_id, token, autoplay, audio):
        if self.active_song_id != song_id or self._selection_revision != token or self.closing:
            return
        self.audio = audio
        self._awaiting_song_start = None
        self.play_button.setText("▶   Start click")
        self.song_length.setEnabled(True)
        self._sync_transport()
        self.save_active_song()
        if autoplay and self.include_song.isChecked():
            self.toggle_play()
        else:
            self.status.setText("Song audio ready. Press Start.")

    def _library_audio_error(self, song_id, token, text):
        if self.active_song_id == song_id and self._selection_revision == token:
            self._awaiting_song_start = None
            self.play_button.setText("▶   Start click")
            self.status.setText(f"Could not play the song: {text} The saved click is still available.")

    def scale_tempo(self, multiplier):
        value = self.bpm.value() * multiplier
        if 20 <= value <= 300:
            self.bpm.setValue(value)
        else:
            self.status.setText("Choose a tempo between 20 and 300 BPM.")

    def tap(self):
        now = time.monotonic()
        if self.taps and now - self.taps[-1] > 3.2:
            self.taps = []
        self.taps = (self.taps + [now])[-9:]
        if len(self.taps) >= 3:
            tempo = 60 / float(np.median(np.diff(self.taps)))
            if 20 <= tempo <= 300:
                self.bpm.setValue(round(tempo, 1))
                self.status.setText(f"Tapped tempo: {tempo:.1f} BPM. Keep tapping to refine it.")
        else:
            self.status.setText("Keep tapping a steady beat…")

    def settings(self, use_offset=False):
        return ClickSettings(bpm=self.bpm.value(), beats_per_bar=self.meter.currentData(),
            subdivision=self.subdivision.currentData(), accent=self.accent.isChecked(),
            volume=self.volume.value()/100, offset_seconds=self.offset.value() if use_offset else 0,
            sound=self.sound.currentText())

    def toggle_play(self):
        if self.closing:
            return
        if self._awaiting_song_start is not None:
            self.stop()
            self.status.setText("Start cancelled. Press Start when you’re ready.")
            return
        if self.engine.running:
            self.stop()
            return
        self._begin_playback()

    def _begin_playback(self):
        with_song = self.include_song.isChecked()
        if with_song and self.audio is None:
            self._request_song_audio(autoplay=True)
            return
        duration = self._song_duration()
        if duration <= 0:
            self._selected_position = 0.0
        elif self._selected_position >= duration:
            self._selected_position = self.transport.loop_start_seconds if self.transport.loop_enabled else 0.0
        if self.transport.loop_enabled and not self.transport.loop_start_seconds <= self._selected_position < self.transport.loop_end_seconds:
            self._selected_position = self.transport.loop_start_seconds
        self.transport.set_position(self._selected_position)
        try:
            self.engine.start(self.settings(duration > 0), audio=self.audio if with_song else None,
                include_song=with_song, device=self.device.currentData(),
                duration_seconds=duration if duration > 0 else None,
                start_seconds=self._selected_position,
                loop_start_seconds=self.transport.loop_start_seconds if self.transport.loop_enabled else None,
                loop_end_seconds=self.transport.loop_end_seconds if self.transport.loop_enabled else None)
        except Exception as exc:
            logging.exception("Playback failed")
            self.show_error("Could not play audio", str(exc))
            return
        self._playback_active = True
        self._update_play_label()
        self.clock.setText("Space to pause")
        self.status.setText("Practice loop playing. Space to pause." if self.transport.loop_enabled else
            "Playing song + click. Space to pause." if with_song else "Click playing. Space to pause.")

    def stop(self, reset_position=False):
        if self.engine.running or self._playback_active:
            self._selected_position = self.engine.position_seconds
        self.engine.stop()
        self._playback_active = False
        if self._awaiting_song_start is not None:
            self._selection_revision += 1
            self._pending_library_load = None
            self._awaiting_song_start = None
        if reset_position:
            self._selected_position = 0.0
        self.transport.set_position(self._selected_position)
        self._remember_transport()
        self._update_play_label()
        self.clock.setText("Space to play / pause")
        self.status.setText("Paused at the selected position. Press Play to resume.")
        self.beat_strip.current = -1
        self.beat_strip.update()

    def _tick(self):
        if self.engine.running:
            pos = self.engine.position_seconds
            self._selected_position = pos
            if not self.transport.is_interacting:
                self.transport.set_position(pos)
            self.clock.setText("Space to pause" if self._song_duration() else f"{int(pos)//60:02}:{int(pos)%60:02}  ·  Space to pause")
            offset = self.offset.value() if self._song_duration() > 0 else 0
            beat = int((pos-offset)*self.bpm.value()/60) % self.meter.currentData() if pos >= offset else -1
            if beat != self.beat_strip.current:
                self.beat_strip.current = beat
                self.beat_strip.update()
        elif self._playback_active:
            error = self.engine.error
            self.stop()
            self.status.setText("Playback stopped: " + error if error else "Playback finished. Press Start to play again.")

    def choose_song(self):
        initial = str(self.preferences.value("song_folder", Path.home() / "Music"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add songs to your library", initial,
            "Audio files (*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus *.wma *.aiff *.aif);;All files (*)")
        self.add_songs(paths)

    def import_song(self, path):
        self.add_songs([path])

    def add_songs(self, paths):
        if not paths or self.closing or not self.save_active_song():
            return
        existing = {os.path.normcase(os.path.abspath(e["path"])): e for e in self.library.songs}
        pending = {item["path"] for item in self._import_queue}
        if self._import_context:
            pending.add(self._import_context["path"])
        activate = self._import_context is None and not self._import_queue
        for raw_path in paths:
            path = str(Path(raw_path).expanduser().resolve(strict=False))
            key = os.path.normcase(path)
            if key in existing:
                if len(paths) == 1:
                    self.load_library_song(existing[key]["id"])
                continue
            if any(os.path.normcase(p) == key for p in pending):
                continue
            context = dict(path=path, activate=activate, token=None,
                settings=dict(**asdict(ClickSettings(volume=self.volume.value()/100)),
                    duration_seconds=0.0, analyzed=False, confidence="Not analyzed",
                    include_song=False, match_song_length=True))
            if activate:
                self.stop()
                self._selection_revision += 1
                context["token"] = self._selection_revision
                self.active_song_id = None
                self._applying_song = True
                self.audio = self.analysis = None
                self.include_song.setChecked(False)
                self.include_song.setEnabled(False)
                self.song_length.setChecked(False)
                self.song_length.setEnabled(False)
                self.offset.setValue(0)
                self.offset.setEnabled(False)
                self._applying_song = False
                self._restore_transport()
                self.song_name.setText(Path(path).name)
                self.result_label.setText("Finding the beat…")
                self.analysis_note.setText("Reading your audio and measuring its rhythm.")
                self.refresh_library()
                activate = False
            self._import_queue.append(context)
            pending.add(path)
        self._start_next_import()

    def _start_next_import(self):
        if self._import_context is not None or not self._import_queue or self.closing:
            return
        self._import_context = self._import_queue.pop(0)
        self._imported_audio = None
        path = self._import_context["path"]
        self.preferences.setValue("song_folder", str(Path(path).parent))
        self.import_button.setEnabled(False)
        self.library_add_button.setEnabled(False)
        job = Job("song", path)
        job.song_ready.connect(self._song_loaded)
        job.result.connect(self._song_analyzed)
        job.error.connect(self._song_error)
        self.run_job(job)

    @Slot(object)
    def _song_loaded(self, audio):
        self._imported_audio = audio

    @Slot(object)
    def _song_analyzed(self, result):
        if self._import_context:
            self._finish_import(result=result)

    @Slot(str)
    def _song_error(self, text):
        if self._imported_audio is not None:
            self._finish_import(error=text)
        elif self._import_context:
            context = self._import_context
            if context["activate"] and context["token"] == self._selection_revision:
                self.result_label.setText("Set the tempo by ear")
                self.analysis_note.setText(text)
            self.status.setText(f"Could not add {Path(context['path']).name}: {text}")

    def _finish_import(self, result=None, error=None):
        context = self._import_context
        audio = self._imported_audio
        fields = dict(context["settings"], duration_seconds=audio.duration)
        if result is not None:
            fields.update(bpm=result.bpm, offset_seconds=result.first_beat_seconds,
                          confidence=result.confidence, analyzed=True)
        saved = True
        try:
            entry = self.library.add(context["path"], **fields)
            self._library_last_error = None
        except (OSError, ValueError, KeyError) as exc:
            entry = dict(fields, id=None, title=Path(context["path"]).stem, path=context["path"])
            saved = False
            self._library_error(exc)
        if context["activate"] and context["token"] == self._selection_revision and not self.closing:
            self._apply_library_song(entry, audio=audio)
            self.analysis = result
            if result is not None:
                self.analysis_note.setText(f"{result.confidence} confidence. {result.message} Changes save with this song.")
            elif error:
                self.result_label.setText("Set the tempo by ear")
                self.analysis_note.setText(error + " Tap or type its BPM; your correction will be saved.")
        else:
            self.refresh_library()
        if saved:
            self.status.setText(f"Added {entry['title']} to your library." + (" Set its BPM by ear." if error else " Select a song to load its click track."))
        else:
            self.status.setText("Song loaded, but the library could not be saved. Your existing library was preserved.")

    def export_wav(self):
        matched = self.song_length.isChecked() and self._song_duration() > 0
        duration = self._song_duration() if matched else self.minutes.value()*60 + self.seconds.value()
        if duration <= 0:
            self.show_error("Choose a length", "Set a click track length greater than zero.")
            return
        default_output = export_directory()
        default_output.mkdir(parents=True, exist_ok=True)
        destination_dir = str(self.preferences.value("export_folder", default_output))
        default = f"ClickTrack_{self.bpm.value():g}BPM.wav"
        path, _ = QFileDialog.getSaveFileName(self, "Save click-only WAV", str(Path(destination_dir) / default), "WAV audio (*.wav)")
        if not path:
            return
        if not path.lower().endswith(".wav"):
            path += ".wav"
        entry = self.library.get(self.active_song_id)
        original_path = self.audio.path if self.audio else entry["path"] if entry else None
        protected_paths = {Path(song["path"]).resolve() for song in self.library.songs}
        if original_path:
            protected_paths.add(Path(original_path).resolve())
        if Path(path).resolve() in protected_paths:
            self.show_error("Choose a new filename", "Save your click track separately from the original song.")
            return
        self.preferences.setValue("export_folder", str(Path(path).parent))
        job = Job("export", path, self.settings(matched), duration)
        job.result.connect(self._export_done)
        job.error.connect(lambda text: self.show_error("Could not save WAV", text))
        self.export_button.setEnabled(False)
        self.run_job(job)

    @Slot(object)
    def _export_done(self, path):
        self.last_export = str(path)
        self.show_export.show()
        self.status.setText(f"Saved click-only WAV: {path}")

    def reveal_export(self):
        if self.last_export:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.last_export).parent)))

    def run_job(self, job):
        thread = QThread(self)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.progress.connect(self._progress)
        job.finished.connect(thread.quit)
        job.finished.connect(job.deleteLater)
        thread.finished.connect(lambda: self._job_done(thread, job))
        self.jobs.append((thread, job))
        self.progress.setRange(0, 0)
        self.progress.show()
        thread.start()

    @Slot(int, str)
    def _progress(self, value, message):
        if value > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, value))
        self.status.setText(message)

    def _job_done(self, thread, job):
        kind = job.kind
        self.jobs = [(t,j) for t,j in self.jobs if t is not thread]
        if kind == "song":
            self._import_context = None
            self._imported_audio = None
        importing = any(j.kind == "song" for _,j in self.jobs) or bool(self._import_queue)
        self.import_button.setEnabled(not importing)
        self.library_add_button.setEnabled(not importing)
        self.export_button.setEnabled(not any(j.kind == "export" for _,j in self.jobs))
        thread.deleteLater()
        if not self.closing:
            self._start_next_import()
            self._start_pending_audio_load()
        if not self.jobs:
            self.progress.hide()
            if self.closing:
                QTimer.singleShot(0, self.close)

    def show_error(self, title, text):
        self.status.setText(text)
        QMessageBox.warning(self, title, text)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.add_songs(paths)
            event.acceptProposedAction()

    def closeEvent(self, event):
        self.stop()
        if not self.save_active_song():
            # A stale or unwritable library must not trap the window open.
            # Keep a separate recovery copy without replacing the saved library.
            try:
                songs = self.library.songs
                for song in songs:
                    if song["id"] == self.active_song_id:
                        song.update(asdict(self.settings(True)),
                            include_song=self.include_song.isChecked() if self.include_song.isEnabled() else song["include_song"],
                            match_song_length=self.song_length.isChecked())
                        if self._tempo_edited:
                            song.update(analyzed=True, confidence="Manual")
                recovery = self.library.path.with_name(f"library-recovery-{uuid.uuid4().hex[:12]}.json")
                with recovery.open("x", encoding="utf-8") as stream:
                    json.dump({"version": 1, "songs": songs}, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.show_error("Song changes saved separately",
                    f"The main library could not be updated. Your latest settings were saved in:\n{recovery}\n\nYour existing library was preserved.")
            except OSError as exc:
                self.show_error("Song changes could not be saved",
                    f"Your existing library was preserved, but the latest changes could not be saved: {exc}")
            self.active_song_id = None
        if self.jobs:
            self.closing = True
            self._import_queue.clear()
            self.centralWidget().setEnabled(False)
            self.status.setText("Finishing the current audio task before closing…")
            event.ignore()
            return
        p = self.preferences
        for key, value in [("bpm",self.bpm.value()), ("volume",self.volume.value()),
                           ("meter",self.meter.currentIndex()), ("subdivision",self.subdivision.currentIndex()),
                           ("sound",self.sound.currentIndex()), ("accent",self.accent.isChecked())]:
            p.setValue(key, value)
        p.sync()
        event.accept()


def main():
    if "--self-test" in sys.argv[1:]:
        from release_smoke import run_self_test
        return run_self_test(sys.modules[__name__], sys.argv[1:])
    configure_runtime_temp()
    log_folder = data_directory()
    log_folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_folder / "clicktrack.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow(library_path=os.environ.get("CLICKTRACK_LIBRARY_PATH"))
    window.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        QTimer.singleShot(300, lambda: window.import_song(sys.argv[1]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
