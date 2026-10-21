"""Native song-position and beat-snapped practice-loop controls."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QSignalBlocker, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox,
    QCheckBox, QPushButton, QSizePolicy,
)


def _time_text(seconds):
    total = max(0, round(float(seconds) * 1000))
    minutes, milliseconds = divmod(total, 60_000)
    whole, fraction = divmod(milliseconds, 1000)
    return f"{minutes:02}:{whole:02}.{fraction:03}"


class _Timeline(QWidget):
    """Seek in the main track; drag A/B below it to adjust the loop."""

    def __init__(self, transport):
        super().__init__(transport)
        self.transport = transport
        self.dragging = False
        self.drag_kind = None
        self.setMinimumHeight(56)
        self.setMaximumHeight(62)
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Song timeline with draggable loop start and end brackets")
        self.setToolTip("Click or drag to choose where playback starts. Drag the A/B brackets to choose a practice loop.")

    def sizeHint(self):
        return QSize(350, 56)

    def time_to_x(self, seconds):
        duration = self.transport.duration_seconds
        fraction = float(seconds) / duration if duration > 0 else 0
        return 12.0 + max(0.0, min(1.0, fraction)) * max(1, self.width() - 24)

    def x_to_time(self, x):
        fraction = (float(x) - 12) / max(1, self.width() - 24)
        return max(0.0, min(1.0, fraction)) * self.transport.duration_seconds

    def paintEvent(self, event):
        t = self.transport
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.isEnabled() and t.duration_seconds > 0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#111925"))
        p.drawRoundedRect(QRectF(5, 14, self.width() - 10, 27), 7, 7)
        if not active:
            p.setPen(QColor("#6e7b91"))
            p.drawText(QRectF(5, 12, self.width()-10, 30), Qt.AlignmentFlag.AlignCenter, "Choose a song to seek or loop")
            return
        a, b = self.time_to_x(t.loop_start_seconds), self.time_to_x(t.loop_end_seconds)
        shade = QColor("#3f655f" if t.loop_enabled else "#2e3d53")
        shade.setAlpha(160 if t.loop_enabled else 100)
        p.setBrush(shade)
        p.drawRoundedRect(QRectF(a, 16, max(1, b-a), 23), 3, 3)
        # Show a representative beat/bar grid while bounding paint work on long songs.
        period = t.beat_seconds
        first = max(0, math.ceil(-t.offset_seconds / period))
        last = math.floor((t.duration_seconds-t.offset_seconds) / period)
        visible_beats = max(0, last-first+1)
        stride = max(1, math.ceil(visible_beats / max(1, self.width()/5)))
        if stride > 1:
            stride = max(t.beats_per_bar, math.ceil(stride/t.beats_per_bar)*t.beats_per_bar)
        for beat in range(first, last+1, stride):
            x = self.time_to_x(t.offset_seconds + beat*period)
            downbeat = beat % t.beats_per_bar == 0
            p.setPen(QPen(QColor("#637d8d" if downbeat else "#3b5063"), 1))
            p.drawLine(QPointF(x, 18 if downbeat else 23), QPointF(x, 37))
        # A/B handles use a lower grab area so a playhead at A is still easy to seek.
        handle_color = QColor("#a99bf5" if t.loop_enabled else "#91a0bb")
        p.setPen(QPen(handle_color, 2))
        p.drawLine(QPointF(a, 16), QPointF(a, 48))
        p.drawLine(QPointF(a, 16), QPointF(a+8, 16))
        p.drawLine(QPointF(b, 16), QPointF(b, 48))
        p.drawLine(QPointF(b-8, 16), QPointF(b, 16))
        p.setPen(handle_color)
        p.drawText(QRectF(a-14, 40, 14, 15), Qt.AlignmentFlag.AlignCenter, "A")
        p.drawText(QRectF(b, 40, 14, 15), Qt.AlignmentFlag.AlignCenter, "B")
        x = self.time_to_x(t.position_seconds)
        p.setPen(QPen(QColor("#91fff0"), 2))
        p.drawLine(QPointF(x, 11), QPointF(x, 37))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#91fff0"))
        p.drawPolygon(QPolygonF([QPointF(x-5, 4), QPointF(x+5, 4), QPointF(x, 12)]))

    def _hit_kind(self, x, y):
        if y >= 35:
            a = abs(x-self.time_to_x(self.transport.loop_start_seconds))
            b = abs(x-self.time_to_x(self.transport.loop_end_seconds))
            if min(a,b) <= 13:
                return "a" if a <= b else "b"
        return "seek"

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.transport.duration_seconds <= 0:
            return super().mousePressEvent(event)
        self.setFocus()
        self.dragging = True
        self.drag_kind = self._hit_kind(event.position().x(), event.position().y())
        self._preview(event.position().x())
        event.accept()

    def _preview(self, x):
        t = self.transport
        value = self.x_to_time(x)
        if self.drag_kind == "seek":
            t._preview_seek(t.snap_position(value))
        elif self.drag_kind == "a":
            t._preview_loop(value, t.loop_end_seconds, edited="a")
        elif self.drag_kind == "b":
            t._preview_loop(t.loop_start_seconds, value, edited="b")

    def mouseMoveEvent(self, event):
        if self.dragging:
            self._preview(event.position().x())
            event.accept()
        else:
            kind = self._hit_kind(event.position().x(), event.position().y())
            self.setCursor(Qt.CursorShape.SizeHorCursor if kind != "seek" else Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.dragging:
            self._preview(event.position().x())
            kind = self.drag_kind
            self.dragging = False
            self.drag_kind = None
            if kind == "seek":
                self.transport.seekRequested.emit(self.transport.position_seconds)
            else:
                self.transport._finish_user_loop()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        t = self.transport
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            t._step_beat(-1 if event.key() == Qt.Key.Key_Left else 1)
        elif event.key() == Qt.Key.Key_Home:
            t._commit_seek(0)
        elif event.key() == Qt.Key.Key_End:
            t._commit_seek(t.duration_seconds)
        else:
            return super().keyPressEvent(event)
        event.accept()


class SongTransport(QWidget):
    """Beat-aware transport with silent programmatic setters.

    ``seekRequested`` fires once when a user commits a seek. ``loopChanged``
    fires once when loop bounds or the enabled checkbox are committed. The
    timeline shows preview movement while dragging without restarting audio.
    """
    seekRequested = Signal(float)
    loopChanged = Signal(bool, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0.0
        self._bpm = 120.0
        self._offset = 0.0
        self._beats_per_bar = 4
        self._position = 0.0
        self._loop_start = 0.0
        self._loop_end = 0.0
        self.setObjectName("transparent")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.timeline = _Timeline(self)
        layout.addWidget(self.timeline)
        row = QHBoxLayout()
        row.setSpacing(5)
        self.time_label = QLabel("00:00.000 / 00:00.000")
        self.time_label.setObjectName("muted")
        self.time_label.setStyleSheet("font-size: 11px;")
        row.addWidget(self.time_label, 1)
        self.previous_beat_button = self._button("‹", "Previous beat", lambda: self._step_beat(-1))
        self.next_beat_button = self._button("›", "Next beat", lambda: self._step_beat(1))
        self.position_spin = self._spin("Playback position in seconds")
        self.position_spin.setMinimumWidth(82)
        self.position_spin.setMaximumWidth(100)
        self.position_spin.valueChanged.connect(self._commit_seek)
        row.addWidget(self.previous_beat_button)
        row.addWidget(self.position_spin)
        row.addWidget(self.next_beat_button)
        layout.addLayout(row)
        check_row = QHBoxLayout()
        check_row.setSpacing(14)
        self.snap_checkbox = QCheckBox("Snap to beat")
        self.snap_checkbox.setChecked(True)
        self.snap_checkbox.setToolTip("Snap seeks and loop brackets to the nearest main beat in the song.")
        self.loop_checkbox = QCheckBox("Loop section")
        self.loop_checkbox.toggled.connect(self._loop_toggled)
        self.snap_checkbox.toggled.connect(self._snap_toggled)
        check_row.addWidget(self.snap_checkbox)
        check_row.addWidget(self.loop_checkbox)
        check_row.addStretch()
        layout.addLayout(check_row)
        bounds_row = QHBoxLayout()
        bounds_row.setSpacing(5)
        self.loop_start_spin = self._spin("Loop start A in seconds")
        self.loop_start_spin.setMinimumWidth(78)
        self.loop_start_spin.setPrefix("A  ")
        self.loop_end_spin = self._spin("Loop end B in seconds (excluded from the loop)")
        self.loop_end_spin.setMinimumWidth(78)
        self.loop_end_spin.setPrefix("B  ")
        self.loop_start_spin.valueChanged.connect(lambda value: self._edit_loop(value, self._loop_end, "a"))
        self.loop_end_spin.valueChanged.connect(lambda value: self._edit_loop(self._loop_start, value, "b"))
        self.set_a_button = self._button("Set A", "Set loop start to the current song position", self._set_a)
        self.set_b_button = self._button("Set B", "Set loop end to the current song position", self._set_b)
        bounds_row.addWidget(self.loop_start_spin, 1)
        bounds_row.addWidget(self.set_a_button)
        bounds_row.addWidget(self.loop_end_spin, 1)
        bounds_row.addWidget(self.set_b_button)
        layout.addLayout(bounds_row)
        self._sync_controls()
        self.setEnabled(False)

    def _button(self, text, tooltip, callback):
        widget = QPushButton(text)
        widget.setObjectName("transportButton")
        widget.setToolTip(tooltip)
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.setStyleSheet("QPushButton { padding: 5px 7px; border-radius: 6px; min-height: 18px; }")
        widget.clicked.connect(callback)
        return widget

    def _spin(self, tooltip):
        widget = QDoubleSpinBox()
        widget.setDecimals(3)
        widget.setRange(0, 0)
        widget.setSingleStep(.001)
        widget.setSuffix(" s")
        widget.setKeyboardTracking(False)
        widget.setToolTip(tooltip)
        widget.setStyleSheet("QDoubleSpinBox { padding: 4px 5px; min-height: 18px; }")
        return widget

    @property
    def duration_seconds(self):
        return self._duration

    @property
    def position_seconds(self):
        return self._position

    @property
    def loop_enabled(self):
        return self.loop_checkbox.isChecked() and self._duration > 0

    @property
    def loop_start_seconds(self):
        return self._loop_start

    @property
    def loop_end_seconds(self):
        return self._loop_end

    @property
    def offset_seconds(self):
        return self._offset

    @property
    def beats_per_bar(self):
        return self._beats_per_bar

    @property
    def beat_seconds(self):
        return 60.0/self._bpm

    @property
    def is_interacting(self):
        return self.timeline.dragging or any(
            (spin.hasFocus() or spin.lineEdit().hasFocus()) and spin.lineEdit().isModified()
            for spin in (self.position_spin, self.loop_start_spin, self.loop_end_spin))

    def set_song(self, duration_seconds, bpm, offset_seconds, beats_per_bar=4):
        values = (float(duration_seconds), float(bpm), float(offset_seconds))
        if not all(math.isfinite(value) for value in values) or values[0] < 0 or not 20 <= values[1] <= 300 or values[2] < 0:
            raise ValueError("Choose a valid song length, tempo, and first-click offset.")
        if int(beats_per_bar) != beats_per_bar or not 1 <= beats_per_bar <= 16:
            raise ValueError("Choose a valid number of beats per bar.")
        previous_duration = self._duration
        previous_bpm, previous_offset = self._bpm, self._offset
        resnap_loop = self.loop_enabled and self.snap_checkbox.isChecked() and (
            values[1] != previous_bpm or values[2] != previous_offset)
        self._duration, self._bpm, self._offset = values
        self._beats_per_bar = int(beats_per_bar)
        self._position = self._clamp_position(self._position)
        if previous_duration <= 0:
            self._loop_start, self._loop_end = 0.0, self._duration
        else:
            self._loop_start, self._loop_end = self._normalize_loop(
                self._loop_start, self._loop_end, snap=resnap_loop)
        self._sync_controls()
        self._update_loop_availability()
        self.setEnabled(self._duration > 0)

    def set_position(self, seconds):
        if self.is_interacting:
            return
        self._position = self._clamp_position(seconds, allow_end=True)
        self._sync_controls()

    def set_loop(self, enabled, start_seconds, end_seconds):
        self._loop_start, self._loop_end = self._normalize_loop(start_seconds, end_seconds)
        with QSignalBlocker(self.loop_checkbox):
            self.loop_checkbox.setChecked(bool(enabled) and self._duration > 0
                and (not self.snap_checkbox.isChecked() or self._has_full_beat()))
        self._sync_controls()

    def set_enabled(self, enabled):
        self.setEnabled(bool(enabled) and self._duration > 0)

    def reset(self):
        self._duration = self._position = self._loop_start = self._loop_end = 0.0
        self.timeline.dragging = False
        self.timeline.drag_kind = None
        with QSignalBlocker(self.loop_checkbox):
            self.loop_checkbox.setChecked(False)
        self._sync_controls()
        self.setEnabled(False)

    def _clamp_position(self, seconds, allow_end=False):
        value = float(seconds)
        if not math.isfinite(value):
            raise ValueError("Choose a valid song position.")
        maximum = self._duration if allow_end else max(0, self._duration-1/44100)
        return min(maximum, max(0.0, value))

    def snap_position(self, seconds, allow_end=False):
        value = self._clamp_position(seconds, allow_end)
        if not self.snap_checkbox.isChecked() or self._duration <= 0:
            return value
        maximum = self._duration if allow_end else max(0, self._duration-1/44100)
        last_beat = math.floor((maximum-self._offset)/self.beat_seconds+1e-9)
        if last_beat < 0:
            return value
        beat = math.floor((value-self._offset)/self.beat_seconds+.5)
        beat = max(0, min(last_beat, beat))
        return self._clamp_position(self._offset+beat*self.beat_seconds, allow_end)

    def _normalize_loop(self, start, end, edited=None, snap=False):
        if self._duration <= 0:
            return 0.0, 0.0
        a = self._clamp_position(start, allow_end=True)
        b = self._clamp_position(end, allow_end=True)
        if snap:
            a, b = self.snap_position(a), self.snap_position(b, allow_end=True)
        span = min(self._duration, self.beat_seconds if snap and self.snap_checkbox.isChecked() else .001)
        if b-a < span-1e-8:
            if edited == "a":
                a = max(0.0, b-span)
                if snap and a >= self._offset:
                    a = self._offset+max(0, math.floor((a-self._offset)/self.beat_seconds+1e-8))*self.beat_seconds
            elif edited == "b":
                b = min(self._duration, a+span)
                if snap and b < self._duration:
                    b = min(self._duration, self._offset+max(0, math.ceil((b-self._offset)/self.beat_seconds-1e-8))*self.beat_seconds)
            if b-a < span-1e-8:
                a, b = 0.0, self._duration
                if snap and self._offset < self._duration:
                    a, b = self.snap_position(a), self.snap_position(b, allow_end=True)
                    if b-a < min(span,self._duration)-1e-8:
                        a, b = 0.0, self._duration
        return a, b

    def _has_full_beat(self):
        return self._offset+self.beat_seconds <= self._duration+1e-9

    def _update_loop_availability(self):
        available = self._duration > 0 and (not self.snap_checkbox.isChecked() or self._has_full_beat())
        self.loop_checkbox.setEnabled(available)
        if not available:
            with QSignalBlocker(self.loop_checkbox):
                self.loop_checkbox.setChecked(False)
        self.loop_checkbox.setToolTip("Repeat playback between the A and B brackets." if available else
            "A complete beat must fit between A and B. Turn off Snap to beat for a shorter or unaligned loop.")

    def _sync_controls(self):
        for spin, value in ((self.position_spin,self._position),
                            (self.loop_start_spin,self._loop_start), (self.loop_end_spin,self._loop_end)):
            with QSignalBlocker(spin):
                spin.setMaximum(self._duration)
                spin.setValue(value)
                spin.lineEdit().setModified(False)
        self.time_label.setText(f"{_time_text(self._position)} / {_time_text(self._duration)}")
        self.timeline.update()

    def _preview_seek(self, value):
        self._position = self._clamp_position(value)
        self._sync_controls()

    def _commit_seek(self, seconds):
        if self._duration <= 0:
            return
        self._preview_seek(self.snap_position(seconds))
        self.seekRequested.emit(self._position)

    def _step_beat(self, direction):
        if self._duration <= 0:
            return
        relative = (self._position-self._offset)/self.beat_seconds
        beat = math.floor(relative+1e-7)+1 if direction > 0 else math.ceil(relative-1e-7)-1
        target = self._offset+beat*self.beat_seconds if beat >= 0 else 0.0
        # Beat-step buttons always move by beats, even when free seeking is enabled.
        self._preview_seek(self._clamp_position(target))
        self.seekRequested.emit(self._position)

    def _preview_loop(self, start, end, edited):
        self._loop_start, self._loop_end = self._normalize_loop(start,end,edited,True)
        self._sync_controls()

    def _finish_user_loop(self):
        enabled = self._duration > 0 and (not self.snap_checkbox.isChecked() or self._has_full_beat())
        with QSignalBlocker(self.loop_checkbox):
            self.loop_checkbox.setChecked(enabled)
        self._sync_controls()
        self.loopChanged.emit(self.loop_enabled,self._loop_start,self._loop_end)

    def _edit_loop(self, start, end, edited):
        if self._duration <= 0:
            return
        self._preview_loop(start,end,edited)
        self._finish_user_loop()

    def _set_a(self):
        self._edit_loop(self._position,self._loop_end,"a")

    def _set_b(self):
        self._edit_loop(self._loop_start,self._position,"b")

    def _loop_toggled(self, enabled):
        if enabled and self.snap_checkbox.isChecked() and not self._has_full_beat():
            with QSignalBlocker(self.loop_checkbox):
                self.loop_checkbox.setChecked(False)
        if enabled:
            self._loop_start,self._loop_end = self._normalize_loop(self._loop_start,self._loop_end,snap=True)
        self._sync_controls()
        self.loopChanged.emit(self.loop_enabled,self._loop_start,self._loop_end)

    def _snap_toggled(self, checked):
        was_enabled = self.loop_enabled
        self._update_loop_availability()
        if checked and was_enabled and not self._has_full_beat():
            with QSignalBlocker(self.loop_checkbox):
                self.loop_checkbox.setChecked(False)
            self._sync_controls()
            self.loopChanged.emit(False,self._loop_start,self._loop_end)
            return
        if checked and self.loop_enabled:
            self._loop_start,self._loop_end = self._normalize_loop(self._loop_start,self._loop_end,snap=True)
            self._sync_controls()
            self.loopChanged.emit(self.loop_enabled,self._loop_start,self._loop_end)
