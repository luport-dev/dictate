#!/usr/bin/env python3
"""Dictate GUI — PyQt6 tray app for offline voice dictation (Parakeet)."""
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QLockFile, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPushButton, QSpinBox, QSystemTrayIcon, QTextEdit,
    QVBoxLayout, QWidget,
)
from pynput import keyboard

import core


def _make_listener(on_press, on_release):
    """Prefer evdev (works on Wayland+Xorg); fall back to pynput on failure.
    Windows has no evdev, so it goes straight to pynput."""
    try:
        if not core.IS_LINUX:
            raise RuntimeError("not on Linux")
        import evdev_listener
        l = evdev_listener.Listener(on_press=on_press, on_release=on_release)
        l.start()
        print("keyboard listener: evdev", file=sys.stderr)
        return l
    except Exception as e:
        print(f"evdev unavailable ({e}); falling back to pynput", file=sys.stderr)
        l = keyboard.Listener(on_press=on_press, on_release=on_release)
        l.daemon = True
        l.start()
        return l


class Bridge(QObject):
    hotkey_press = pyqtSignal()
    hotkey_release = pyqtSignal()
    other_press = pyqtSignal()
    transcribed = pyqtSignal(str)
    error = pyqtSignal(str)
    typing_done = pyqtSignal()
    mic_error = pyqtSignal(str)
    mic_checked = pyqtSignal(bool)
    notify = pyqtSignal(str, str, int)


class SettingsDialog(QDialog):
    # Bridge signals for the background model-download thread.
    _dl_status = pyqtSignal(str)
    _dl_done = pyqtSignal(bool, str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dictate — Settings")
        self.cfg = dict(cfg)

        self.resize(550, 10)   # about 550 px wide; height follows the content
        self.form = QFormLayout()

        self.mode_box = QComboBox()
        self.mode_box.addItem("Push-to-talk (hold key)", "ptt")
        self.mode_box.addItem("Toggle (tap to start, tap to stop)", "toggle")
        i = self.mode_box.findData(self.cfg.get("mode", "ptt"))
        self.mode_box.setCurrentIndex(max(i, 0))
        self.form.addRow("Mode:", self.mode_box)

        self.key_box = QComboBox()
        for k in core.KEY_MAP:
            if k.startswith("mouse_") and not core.IS_LINUX:
                continue   # mouse side buttons need the evdev backend
            self.key_box.addItem(k.upper().replace("_", " "), k)
        i = self.key_box.findData(self.cfg.get("key", "f9"))
        self.key_box.setCurrentIndex(max(i, 0))
        self.form.addRow("Hotkey:", self.key_box)

        self.paste_box = QComboBox()
        self.paste_box.addItem("Applications (Ctrl+V)", "ctrl+v")
        self.paste_box.addItem("Terminal / Codex CLI (Ctrl+Shift+V)", "ctrl+shift+v")
        i = self.paste_box.findData(self.cfg.get("paste_shortcut", "ctrl+v"))
        self.paste_box.setCurrentIndex(max(i, 0))
        self.paste_box.setToolTip(
            "Wayland only: choose Terminal when dictating into Codex CLI.\n"
            "Choose Applications for browser and editor text fields."
        )
        if core.IS_LINUX and os.environ.get("XDG_SESSION_TYPE") != "x11":
            self.form.addRow("Paste into:", self.paste_box)

        # --- Local model ---
        import os as _os
        cpu_max = max(1, _os.cpu_count() or 1)
        self.local_status_label = QLabel()
        self.local_status_label.setWordWrap(True)
        self.form.addRow("Local model:", self.local_status_label)
        self.local_download_btn = QPushButton()
        self.local_download_btn.clicked.connect(self.on_download_model)
        self.form.addRow("", self.local_download_btn)
        self.local_threads_box = QSpinBox()
        self.local_threads_box.setRange(1, cpu_max)
        self.local_threads_box.setValue(int(self.cfg.get("local_stt_num_threads", min(4, cpu_max))))
        self.local_threads_box.setToolTip(
            "sherpa-onnx CPU thread count. 4 = best latency/resource balance on\n"
            "this system; more brings little. Changing this rebuilds the recognizer."
        )
        self.form.addRow("CPU threads:", self.local_threads_box)

        self.mic_box = QComboBox()
        self.mic_box.addItem("System default", "")
        for label, name in core.list_input_devices():
            self.mic_box.addItem(label, name)
        saved = self.cfg.get("input_device", "")
        if saved and self.mic_box.findData(saved) < 0:
            self.mic_box.addItem(f"{core.friendly_device_name(saved)} (not connected)", saved)
        self.mic_box.setCurrentIndex(max(self.mic_box.findData(saved), 0))
        # Long device names must not stretch the dialog; elide them instead.
        self.mic_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mic_box.setMinimumContentsLength(20)
        self.form.addRow("Microphone:", self.mic_box)

        self.threshold_box = QDoubleSpinBox()
        self.threshold_box.setRange(0.0, 5.0)
        self.threshold_box.setSingleStep(0.1)
        self.threshold_box.setDecimals(1)
        self.threshold_box.setSuffix(" s")
        self.threshold_box.setValue(float(self.cfg.get("threshold", 0.0)))
        self.threshold_box.setToolTip(
            "Recording starts immediately on press; release sends for transcription.\n"
            "If you release before this duration, the recording is discarded. 0 =\n"
            "always send. Recommended ~2.0s when bound to Ctrl/Alt/Shift so brief\n"
            "taps and shortcuts don't transcribe."
        )
        self.form.addRow("Min hold to send:", self.threshold_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(self.form)

        self.hint = QLabel()
        self.hint.setOpenExternalLinks(True)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #777; padding: 6px 0;")
        layout.addWidget(self.hint)

        layout.addWidget(buttons)

        # Wire the download-worker bridge, then show the model state.
        self._dl_status.connect(self.local_status_label.setText)
        self._dl_done.connect(self._on_download_done)
        self.hint.setText(
            "Runs entirely offline on your CPU. Model: parakeet-primeline "
            "(German, ~640 MB, CC-BY-4.0). Attribution: primeline · NVIDIA · "
            "<a href='https://github.com/k2-fsa/sherpa-onnx'>k2-fsa/sherpa-onnx</a>."
        )
        self._refresh_local_status()

    def _refresh_local_status(self):
        try:
            import local_stt
            installed = local_stt.model_installed()
        except ImportError:
            self.local_status_label.setText(
                "sherpa-onnx not installed — run: pip install --user sherpa-onnx soundfile"
            )
            self.local_download_btn.setEnabled(False)
            self.local_download_btn.setText("Download model (~640 MB)")
            return
        if installed:
            self.local_status_label.setText("Installed and ready.")
            self.local_download_btn.setEnabled(True)
            self.local_download_btn.setText("Re-download model")
        else:
            self.local_status_label.setText("Not installed.")
            self.local_download_btn.setEnabled(True)
            self.local_download_btn.setText("Download model (~640 MB)")

    def on_download_model(self):
        self.local_download_btn.setEnabled(False)
        self.local_download_btn.setText("Downloading...")
        self.local_status_label.setText("Starting download...")
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        try:
            import local_stt
            local_stt.download_model(progress_cb=lambda m: self._dl_status.emit(m))
            self._dl_done.emit(True, "Installed.")
        except Exception as e:
            self._dl_done.emit(False, f"Download failed: {e}")

    def _on_download_done(self, ok, msg):
        self.local_status_label.setText(msg)
        self.local_download_btn.setEnabled(True)
        self.local_download_btn.setText("Re-download model" if ok else "Retry download")

    def values(self):
        return {
            "mode":                  self.mode_box.currentData(),
            "key":                   self.key_box.currentData(),
            "threshold":             float(self.threshold_box.value()),
            "local_stt_num_threads": int(self.local_threads_box.value()),
            "input_device":          self.mic_box.currentData(),
            "paste_shortcut":        self.paste_box.currentData(),
        }


# Equalizer tray icon, same look and motion as the voice-ink project
# (src/voice_ink/tray_icon.py): four bars of up to five gradient segments.
_GRADIENTS = {
    "idle":         [(0,80,20),(0,130,40),(0,185,70),(0,220,140),(0,245,210)],
    "recording":    [(120,0,0),(170,0,0),(220,20,20),(255,60,40),(255,120,80)],
    "transcribing": [(255,100,0),(255,150,0),(255,190,0),(255,225,0),(255,250,50)],
    "typing":       [(0,90,140),(0,130,180),(0,175,220),(40,210,245),(80,240,255)],
}
IDLE_SEGMENTS = (3, 5, 4, 3)
SEGMENT_SLOTS = 5


class BarMotion:
    """Each bar travels one row per frame and turns around at the limits."""

    def __init__(self, count):
        import random
        self._heights = [random.randint(1, SEGMENT_SLOTS) for _ in range(count)]
        self._dirs = [random.choice((-1, 1)) for _ in range(count)]

    def step(self):
        for i, h in enumerate(self._heights):
            nxt = h + self._dirs[i]
            if not 1 <= nxt <= SEGMENT_SLOTS:
                self._dirs[i] = -self._dirs[i]
                nxt = h + self._dirs[i]
            self._heights[i] = nxt
        return tuple(self._heights)


def make_state_icon(state, segments=IDLE_SEGMENTS):
    size, bar_gap, seg_gap = 64, 11, 2
    grad = _GRADIENTS[state]
    bar_w = (size - (len(segments) - 1) * bar_gap) // len(segments)
    seg_h = (size - (SEGMENT_SLOTS - 1) * seg_gap) // SEGMENT_SLOTS
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setPen(Qt.PenStyle.NoPen)
    x = 0
    for height in segments:
        for i in range(height):
            p.setBrush(QColor(*grad[i]))
            bottom = size - i * (seg_h + seg_gap)
            p.drawRoundedRect(x, bottom - seg_h, bar_w, seg_h, 1, 1)
        x += bar_w + bar_gap
    p.end()
    return QIcon(pix)


STATUS_STYLES = {
    "ready":        ("#f3f3f3", "#222"),
    "recording":    ("#fde2e2", "#a00000"),
    "transcribing": ("#fff3cc", "#8a6d00"),
    "typing":       ("#dff3e0", "#1b5e20"),
    "error":        ("#ffd6d6", "#9a0000"),
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dictate")
        self.resize(500, 380)
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.png"))))

        self.cfg = core.load_config()
        self.recorder = core.Recorder()
        self.bridge = Bridge()
        self.bridge.hotkey_press.connect(self.on_press)
        self.bridge.hotkey_release.connect(self.on_release)
        self.bridge.other_press.connect(self.on_other_press)
        self.bridge.transcribed.connect(self.on_transcribed)
        self.bridge.error.connect(self.on_error)
        self.key_held = False
        self.combo = False
        self.recording_active = False
        self.listener = None
        self._target_keys = set()
        self._last_other = None

        self.press_time = 0.0

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Dictate")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        header.addWidget(self.settings_btn)
        v.addLayout(header)

        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.status_label)

        self.info_label = QLabel()
        self.info_label.setStyleSheet("color: #666; padding: 2px 0;")
        v.addWidget(self.info_label)

        v.addWidget(QLabel("Last transcription:"))
        self.last = QTextEdit()
        self.last.setReadOnly(True)
        self.last.setMaximumHeight(110)
        self.last.setStyleSheet(
            "QTextEdit { background: #fafafa; border: 1px solid #ddd; border-radius: 6px; padding: 6px; }"
        )
        v.addWidget(self.last)

        v.addStretch()

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(make_state_icon("idle"), self)
            self.tray.setToolTip("Dictate")
            menu = QMenu()
            a_show = QAction("Show window", self)
            a_show.triggered.connect(self.show_window)
            a_quit = QAction("Quit", self)
            a_quit.triggered.connect(QApplication.quit)
            menu.addAction(a_show)
            menu.addSeparator()
            menu.addAction(a_quit)
            self.tray.setContextMenu(menu)
            self.tray.show()
            if not core.IS_LINUX:
                # No notify-send here; core.notify may run on any thread, so
                # hop to the GUI thread through a signal.
                self.bridge.notify.connect(self.show_tray_message)
                core.notifier = self.bridge.notify.emit

        self._idle_icon = make_state_icon("idle")
        self._motion = None
        self._anim_state = None
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(167)  # 6 fps like voice-ink
        self._anim_timer.timeout.connect(self._anim_tick)
        self.bridge.typing_done.connect(lambda: self.set_status("ready", "Ready"))
        self.bridge.mic_error.connect(self.on_mic_error)
        self.bridge.mic_checked.connect(self.on_mic_checked)
        self._mic_watch = False

        self.set_status("ready", "Ready")
        self.update_info()
        self.start_listener()
        self._preload_local_if_needed()

        if not core.model_ready():
            QTimer.singleShot(250, self.first_run)

    def _preload_local_if_needed(self):
        """Kick off the parakeet recognizer load in the background so the first
        real dictation isn't slowed by the ~0.8s model load + warmup."""
        try:
            import local_stt
        except ImportError:
            return
        if not local_stt.model_installed():
            return
        local_stt.preload(int(self.cfg.get("local_stt_num_threads", local_stt.DEFAULT_THREADS)))

    def first_run(self):
        QMessageBox.information(
            self,
            "Welcome to Dictate",
            "Dictate transcribes offline (German, CPU).\n\n"
            "Open Settings and download the model once (~640 MB).",
        )
        self.open_settings()

    def update_info(self):
        mode_name = "Push-to-talk" if self.cfg["mode"] == "ptt" else "Toggle"
        thr = float(self.cfg.get("threshold", 0.0))
        thr_str = f"     Min hold: {thr:.1f}s" if thr > 0 else ""
        self.info_label.setText(
            f"Mode: {mode_name}     Hotkey: {self.cfg['key'].upper()}     "
            f"Model: {core.MODEL_NAME}{thr_str}"
        )

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cfg.update(dlg.values())
            core.save_config(self.cfg)
            self.restart_listener()
            self.update_info()
            self._preload_local_if_needed()

    def start_listener(self):
        keys = core.KEY_MAP.get(self.cfg["key"])
        if not keys:
            return
        self._target_keys = set(keys)

        def on_press(k):
            try:
                if k in self._target_keys:
                    self.bridge.hotkey_press.emit()
                else:
                    self._last_other = k
                    self.bridge.other_press.emit()
            except Exception:
                pass

        def on_release(k):
            try:
                if k in self._target_keys:
                    self.bridge.hotkey_release.emit()
            except Exception:
                pass

        self.listener = _make_listener(on_press, on_release)

    def restart_listener(self):
        if self.listener:
            self.listener.stop()
            self.listener = None
        self.key_held = False
        self.combo = False
        self.recording_active = False
        self.start_listener()

    def on_press(self):
        if self.key_held:
            return
        self.key_held = True
        self.combo = False
        self.press_time = time.monotonic()
        if self.cfg["mode"] == "ptt":
            if self.start_recording():
                self.recording_active = True
        else:
            if self.recording_active:
                self.stop_and_transcribe()
                self.recording_active = False
            elif self.start_recording():
                self.recording_active = True

    def on_other_press(self):
        if not self.key_held:
            return
        self.combo = True
        # Only abort if this hold started the recording (PTT). In toggle mode
        # a combo while pressing the hotkey shouldn't kill an ongoing session
        # that was started by a previous tap.
        if self.cfg["mode"] == "ptt" and self.recording_active:
            # Offload PipeWire close to keep the GUI thread responsive.
            self._discard_take()
            self.recording_active = False
            self.set_status("ready", "Cancelled (combo)")
            held = time.monotonic() - self.press_time
            core.log.info("cancelled after %.1fs by %s", held, self._last_other)
            # A shortcut like Ctrl+C cancels within a moment and needs no
            # message; a long hold was a dictation that just got thrown away.
            min_hold = float(self.cfg.get("threshold", 0.0))
            if held >= max(min_hold, 1.0):
                core.notify(f"Recording cancelled after {held:.0f}s — another key or mouse button was pressed",
                            urgency="critical", timeout_ms=5000)

    def on_release(self):
        self.key_held = False
        if self.cfg["mode"] != "ptt":
            return
        if not self.recording_active:
            return
        duration = time.monotonic() - self.press_time
        min_hold = float(self.cfg.get("threshold", 0.0))
        if min_hold > 0 and duration < min_hold:
            # Discard branch also blocks on recorder.stop() → freeze. Offload.
            self._discard_take()
            self.recording_active = False
            self.set_status("ready", f"Discarded (held {duration:.1f}s)")
            return
        self.stop_and_transcribe()
        self.recording_active = False

    def start_recording(self) -> bool:
        if not core.model_ready():
            self.set_status("error", "Model not installed — open Settings")
            return False
        self.recorder.device = self.cfg.get("input_device", "")
        try:
            self.recorder.start()
        except Exception as e:
            core.log.exception("could not open the microphone")
            self.on_mic_error(f"Microphone not available: {e}")
            return False
        self.set_status("recording", "Recording...")
        return True

    def stop_and_transcribe(self):
        # recorder.stop() can block for hundreds of ms on PipeWire close,
        # which freezes the GUI ("main on_release doesn't return"). Move the
        # whole pipeline (stop + transcribe) onto a worker thread and update
        # status immediately from the main thread instead.
        self.set_status("transcribing", "Transcribing...")
        # Detach here, on the GUI thread: the take now belongs to the worker,
        # so pressing the hotkey again right away starts a fresh recording
        # instead of being swallowed by the worker's delayed stop.
        take = self.recorder.detach()
        threading.Thread(target=self._stop_and_transcribe_worker, args=(take,), daemon=True).start()

    def _discard_take(self):
        take = self.recorder.detach()
        threading.Thread(target=core.Recorder.finish, args=(take,), daemon=True).start()

    def _stop_and_transcribe_worker(self, take):
        # Short trailing buffer: PipeWire has ~50-100ms input latency and
        # people typically finish the last syllable *after* they release the
        # hotkey. Without this the last word tends to get clipped.
        time.sleep(0.25)
        audio = core.Recorder.finish(take)
        if audio is None:
            core.log.warning("no audio captured (mic delivered no frames)")
            self.recorder.mark_stale()
            self.bridge.mic_error.emit("Microphone not available — no audio captured")
            return
        secs = len(audio) / core.SAMPLE_RATE
        peak = int(np.abs(audio).max())
        t0 = time.monotonic()
        try:
            text = core.transcribe(audio, self.cfg)
        except Exception as e:
            core.log.exception("transcribe failed: %.1fs audio, peak %d", secs, peak)
            self.bridge.error.emit(str(e))
            return
        core.log.info("transcribed %.1fs audio, peak %d, %d chars in %.1fs",
                      secs, peak, len(text or ""), time.monotonic() - t0)
        if not text:
            # Peak is out of 32768; a live mic with speech is in the thousands.
            why = "microphone was silent" if peak < 300 else "no speech recognised"
            if peak < 300:
                self.recorder.mark_stale()
            if peak >= 300:
                core.keep_failed_audio(audio)
            if peak < 300:
                self.bridge.mic_error.emit(f"Microphone not available — {secs:.0f}s of silence")
            else:
                self.bridge.error.emit(f"Nothing transcribed ({secs:.0f}s) — {why}")
            return
        self.bridge.transcribed.emit(text)

    def on_transcribed(self, text):
        if text:
            paste_shortcut = self.cfg.get("paste_shortcut", "ctrl+v")
            def _type():
                try:
                    core.type_text(text, paste_shortcut)
                finally:
                    self.bridge.typing_done.emit()
            self.set_status("typing", "Typing...")
            threading.Thread(target=_type, daemon=True).start()
            self.last.setPlainText(text)
        else:
            self.set_status("ready", "Ready")

    def on_mic_error(self, msg):
        """A dead or vanished microphone (e.g. a USB hub that dropped out):
        report it, then poll until it delivers audio again."""
        self.on_error(msg)
        if not self._mic_watch:
            self._mic_watch = True
            self._mic_tries = 0
            QTimer.singleShot(3000, self._check_mic)

    def _check_mic(self):
        self._mic_tries += 1
        self.recorder.mark_stale()
        dev = self.cfg.get("input_device", "")

        def work():
            self.recorder.reinit_if_idle()
            self.bridge.mic_checked.emit(core.mic_available(dev))
        threading.Thread(target=work, daemon=True).start()

    def on_mic_checked(self, ok):
        if ok:
            self._mic_watch = False
            core.log.info("microphone is available again")
            if not self.recording_active:
                self.set_status("ready", "Ready")
            threading.Thread(target=core.notify, args=("Microphone is back", "low", 2500), daemon=True).start()
        elif self._mic_tries < 100:   # ~5 minutes
            QTimer.singleShot(3000, self._check_mic)
        else:
            self._mic_watch = False

    def on_error(self, msg):
        self.set_status("error", f"Error: {msg[:80]}")
        # The window is usually hidden in the tray, so the status label alone
        # means the dictation fails without anyone noticing.
        threading.Thread(target=core.notify, args=(msg[:200], "critical", 5000), daemon=True).start()

    def show_tray_message(self, msg, urgency, timeout_ms):
        icon = (QSystemTrayIcon.MessageIcon.Critical if urgency == "critical"
                else QSystemTrayIcon.MessageIcon.Information)
        self.tray.showMessage("Dictate", msg, icon, timeout_ms)

    def set_status(self, state, msg):
        bg, fg = STATUS_STYLES.get(state, STATUS_STYLES["ready"])
        self.status_label.setText(msg)
        self.status_label.setStyleSheet(
            f"font-size: 24px; padding: 28px; border-radius: 10px;"
            f"background: {bg}; color: {fg};"
        )
        if self.tray:
            self.tray.setToolTip(f"Dictate — {msg}")
            self._anim_state = state if state in ("recording", "transcribing", "typing") else None
            self._motion = BarMotion(len(IDLE_SEGMENTS))
            if self._anim_state:
                self._anim_tick()
                self._anim_timer.start()
            else:
                self._anim_timer.stop()
                self.tray.setIcon(self._idle_icon)

    def _anim_tick(self):
        if self._anim_state:
            self.tray.setIcon(make_state_icon(self._anim_state, self._motion.step()))

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        # Pure tray app: closing the window only hides it; "Quit" in the tray
        # menu is the way to exit. Without a tray there is no way back, so quit.
        if self.tray:
            event.ignore()
            self.hide()
        else:
            QApplication.quit()


_LOCK = None


def acquire_single_instance_lock():
    """QLockFile works on Linux and Windows alike. A lock left by a crashed
    instance is taken over, because Qt sees that its PID is gone. Not named
    dictate.lock: that empty file from the old flock version would never
    count as stale and block startup forever."""
    global _LOCK
    core.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(core.CONFIG_DIR / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        core.notify("Dictate is already running", urgency="low")
        return False
    _LOCK = lock
    return True


def main():
    # Started via pythonw.exe (the Windows shortcut) there is no console and
    # sys.stderr is None; the model download's progress bar would crash on it.
    if sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")
    if not acquire_single_instance_lock():
        sys.exit(0)
    core.setup_logging()
    core.log.info("started")
    # Let Ctrl+C in the terminal actually kill us — Qt's C++ event loop
    # otherwise never yields to Python's SIGINT handler.
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    app.setApplicationName("Dictate")
    app.setDesktopFileName("dictate")
    has_tray = QSystemTrayIcon.isSystemTrayAvailable()
    app.setQuitOnLastWindowClosed(not has_tray)
    win = MainWindow()
    if not has_tray:
        win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
