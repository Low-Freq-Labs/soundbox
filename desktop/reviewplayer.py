"""Compact review transport over the existing native deck, never a second player."""
from PySide6.QtCore import Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QSlider, QLabel, QStyle


def _time(ms):
    seconds = max(0, int(ms)) // 1000
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


class _Scrubber(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            length = self.style().pixelMetric(QStyle.PM_SliderLength)
            reverse = self.invertedAppearance() ^ (self.layoutDirection() == Qt.RightToLeft)
            value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                round(event.position().x() - length / 2), max(1, self.width() - length), reverse)
            self.setSliderDown(True)
            self.setSliderPosition(value)
        super().mousePressEvent(event)


class ReviewPlayer(QWidget):
    stopped = Signal()

    def __init__(self, parent=None, *, player=None, start=None):
        super().__init__(parent)
        self.player, self.start = player, start
        self._track = None
        self._owned = False
        self._closed = False
        self._error = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 12, 0)
        self.play = QPushButton("▶")
        self.play.setFixedWidth(42)
        self.play.setAutoDefault(False)
        self.play.setAccessibleName("Play or pause review track")
        self.play.clicked.connect(self._toggle)
        layout.addWidget(self.play)
        self.scrub = _Scrubber(Qt.Horizontal)
        self.scrub.setRange(0, 0)
        self.scrub.setMinimumWidth(70)
        self.scrub.setAccessibleName("Review track position")
        self.scrub.sliderMoved.connect(self._dragged)
        self.scrub.sliderReleased.connect(self._seek)
        self.scrub.valueChanged.connect(self._keyboard_seek)
        layout.addWidget(self.scrub, 1)
        self.time = QLabel("0:00 / 0:00")
        self.time.setMinimumWidth(92)
        layout.addWidget(self.time)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._sync)
        self._timer.start()
        if player is not None:
            player.playback_error.connect(self._failed)
        self._sync()

    def set_track(self, tid):
        if tid != self._track:
            self._stop()
            self._track = tid
            self._error = ""
        self._sync()

    def _stop(self):
        if self._owned:
            self._owned = False
            self.player.pause()
            self.stopped.emit()

    def _toggle(self):
        if self._closed or self._track is None or self.player is None:
            return
        self._error = ""
        if not self._owned:
            self._owned = True
            try:
                if not self.start(self._track):
                    self._owned = False
                    self.stopped.emit()
                    self._error = "Audio unavailable"
            except (OSError, ValueError) as exc:
                self._stop()
                self._error = str(exc)
        elif self.player.review_transport_state()["track_id"] != self._track:
            self._stop()  # cancel a pending source-change fade without resuming the old track
        elif self.player.review_transport_state()["requested"]:
            self.player.pause()
        else:
            self.player.resume()
        self._sync()

    def _state(self):
        state = self.player.review_transport_state() if self.player is not None else {}
        if not self._owned:
            return {}
        if state.get("track_id") != self._track:
            return dict(state, requested=True, loading=True, position=0, duration=0)
        return state

    def _dragged(self, value):
        self.time.setText(f"{_time(value)} / {_time(self.scrub.maximum())}")

    def _keyboard_seek(self, _value):
        if not self.scrub.isSliderDown():
            self._seek()

    def _seek(self):
        state = self._state()
        if not self._closed and state and not state["loading"] and state["duration"] > 0:
            self.player.seek_ms(self.scrub.value())
            self._dragged(self.scrub.value())

    def _failed(self, message):
        if self._owned:
            self._error = message
            self._sync()

    def _sync(self):
        if self._closed:
            return
        available = self.player is not None and self.start is not None
        if available:
            available = self.player.review_transport_state()["available"]
        state = self._state()
        self.play.setEnabled(available and self._track is not None)
        self.play.setText("❚❚" if state.get("requested") else "▶")
        self.play.setToolTip("Pause" if state.get("requested") else "Play this library track")
        self.scrub.setEnabled(bool(state and state["duration"] > 0 and not state["loading"]))
        if not self.scrub.isSliderDown():
            with QSignalBlocker(self.scrub):
                self.scrub.setRange(0, state.get("duration", 0))
                self.scrub.setValue(state.get("position", 0))
            self.time.setText(f"{_time(state.get('position', 0))} / {_time(state.get('duration', 0))}")
        if self._error:
            self.time.setText("Audio unavailable")
        elif state.get("loading"):
            self.time.setText("Loading…")
        self.time.setToolTip(self._error)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._stop()
