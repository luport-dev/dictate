"""Shared dictation core: config, recording, transcription, typing."""
import io
import json
import logging
import logging.handlers
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import sys
from pynput import keyboard

SAMPLE_RATE = 16000
CHANNELS = 1

MODEL_NAME = "parakeet-primeline-int8"

CONFIG_DIR = Path.home() / ".config" / "dictate"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = Path.home() / ".local" / "state" / "dictate" / "dictate.log"

log = logging.getLogger("dictate")


def setup_logging():
    """Every recording leaves one line in LOG_PATH, so a dictation that went
    nowhere can be traced afterwards. Never logs transcribed text."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

KEY_MAP = {f"f{i}": {getattr(keyboard.Key, f"f{i}")} for i in range(1, 13)}
KEY_MAP.update({
    "pause":       {keyboard.Key.pause},
    "scroll_lock": {keyboard.Key.scroll_lock},
    "insert":      {keyboard.Key.insert},
    "home":        {keyboard.Key.home},
    "end":         {keyboard.Key.end},
    "page_up":     {keyboard.Key.page_up},
    "page_down":   {keyboard.Key.page_down},
    "caps_lock":   {keyboard.Key.caps_lock},
    "ctrl":        {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
    "ctrl_l":      {keyboard.Key.ctrl_l},
    "ctrl_r":      {keyboard.Key.ctrl_r},
    "alt":         {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r},
    "alt_l":       {keyboard.Key.alt_l},
    "alt_r":       {keyboard.Key.alt_r, keyboard.Key.alt_gr},
    "shift":       {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r},
    "super":       {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r},
    # Mouse-side buttons. Sentinels are tuples emitted by evdev_listener;
    # pynput won't see these (mouse events go through a separate listener) —
    # so these hotkeys only work when the evdev backend is active.
    "mouse_back":    {("mouse", "back")},
    "mouse_forward": {("mouse", "forward")},
    "mouse_side":    {("mouse", "side")},
    "mouse_extra":   {("mouse", "extra")},
    "mouse_task":    {("mouse", "task")},
})

DEFAULT_CONFIG = {
    "mode":                  "ptt",
    "key":                   "f9",
    "threshold":             0.0,
    "local_stt_num_threads": 4,    # sherpa-onnx CPU thread count
}


def model_ready():
    """True once the local ONNX model files have been downloaded."""
    try:
        import local_stt
        return local_stt.model_installed()
    except ImportError:
        return False


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            # Ignore keys from older versions that are no longer supported.
            cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
        except Exception:
            pass
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)
    except Exception:
        pass


def notify(msg, urgency="normal", timeout_ms=1500):
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-t", str(timeout_ms), "Dictate", msg],
            check=False,
        )
    except FileNotFoundError:
        pass


def _paste_wayland(text):
    """Put text on the clipboard and press Ctrl+V through ydotool. ydotool
    'type' sends US-QWERTY scancodes, which garbles umlauts, ? and case on a
    German layout; pasting is layout-independent. Restores the old clipboard.
    Returns False if it could not paste, so the caller can fall back to typing."""
    import time
    try:
        old = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, timeout=2)
        subprocess.run(["wl-copy"], input=text.encode(), check=True, timeout=2)
        # 29 = left ctrl, 47 = v
        r = subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            log.error("ydotool paste key failed: %s", r.stderr.strip())
            return False
        time.sleep(0.3)  # let the target app read the clipboard first
        if old.returncode == 0:
            subprocess.run(["wl-copy"], input=old.stdout, timeout=2)
        else:
            subprocess.run(["wl-copy", "--clear"], timeout=2)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def type_text(text):
    if not text:
        return
    # xdotool respects the active XKB layout (correct German umlauts, no y/z
    # swap on QWERTZ). Prefer it on X11 where it actually works. On Wayland,
    # native windows ignore XTEST so we must fall back to ydotool, which
    # writes raw US-QWERTY scancodes via /dev/uinput — layout-agnostic and
    # therefore wrong for non-US keyboards, but there is no better option
    # for global typing into native Wayland windows today.
    import os
    if os.name == "nt" or sys.platform == "darwin":
        # Windows / macOS: no xdotool or ydotool; pynput types through the
        # OS input API and respects the active keyboard layout.
        keyboard.Controller().type(text)
        return
    on_x11 = os.environ.get("XDG_SESSION_TYPE") == "x11"
    if not on_x11 and _paste_wayland(text):
        return
    tools = ["xdotool", "ydotool"] if on_x11 else ["ydotool", "xdotool"]
    for tool in tools:
        try:
            if tool == "xdotool":
                r = subprocess.run(["xdotool", "type", "--delay", "0", "--", text], check=False)
            else:
                r = subprocess.run(
                    ["ydotool", "type", "--key-delay", "0", "--", text],
                    check=False,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if r.returncode != 0 and r.stderr.strip():
                    log.error("ydotool: %s", r.stderr.strip())
            if r.returncode != 0:
                log.error("%s exited with %s — text not typed", tool, r.returncode)
                notify(f"Typing failed ({tool} exit {r.returncode})", urgency="critical", timeout_ms=5000)
            return
        except FileNotFoundError:
            continue


class Recorder:
    """Each recording is a "take" (stream + its frames). detach() hands the
    current take to the caller without touching PipeWire, so a new start()
    can never collide with a take that is still being finished on a worker
    thread (trailing buffer + slow PipeWire close)."""

    def __init__(self):
        self._take = None
        self._stale = False
        self.lock = threading.Lock()

    def mark_stale(self):
        """The last take got no frames or only silence. PortAudio caches its
        device list, so after a USB/PipeWire device change it keeps opening a
        dead device until it is re-initialised; do that before the next take."""
        self._stale = True

    def _reinit_audio(self):
        self._stale = False
        try:
            sd._terminate()
            sd._initialize()
            log.info("PortAudio re-initialised, default input: %s", sd.query_devices(kind="input")["name"])
        except Exception:
            log.exception("PortAudio re-init failed")

    @property
    def recording(self):
        return self._take is not None

    def start(self):
        with self.lock:
            if self._take is not None:
                return
            if self._stale:
                self._reinit_audio()
            frames = []

            def callback(indata, n, time_info, status):
                frames.append(indata.copy())

            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                callback=callback,
            )
            stream.start()
            self._take = (stream, frames)

    def detach(self):
        """Non-blocking: give up the current take (or None). Safe on the GUI thread."""
        with self.lock:
            take, self._take = self._take, None
            return take

    @staticmethod
    def finish(take):
        """Close a detached take and return its audio, or None if it has none.
        Blocks for hundreds of ms on PipeWire close — call from a worker."""
        if take is None:
            return None
        stream, frames = take
        try:
            stream.stop()
            stream.close()
        except Exception:
            # A vanished mic must not lose what was already captured.
            log.exception("closing input stream failed")
        if not frames:
            return None
        return np.concatenate(frames, axis=0)

    def stop(self):
        return self.finish(self.detach())


def to_wav_bytes(audio):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    buf.seek(0)
    return buf


FAILED_DIR = LOG_PATH.parent / "failed"
FAILED_KEEP = 3


def keep_failed_audio(audio):
    """Save a recording that held sound but came back empty, so the failure
    can be replayed. Only the newest FAILED_KEEP files are kept."""
    try:
        import time
        FAILED_DIR.mkdir(parents=True, exist_ok=True)
        path = FAILED_DIR / time.strftime("%Y%m%d-%H%M%S.wav")
        path.write_bytes(to_wav_bytes(audio).getvalue())
        for old in sorted(FAILED_DIR.glob("*.wav"))[:-FAILED_KEEP]:
            old.unlink()
        log.info("kept failed recording as %s", path)
    except Exception:
        log.exception("could not keep failed recording")


def transcribe(audio, cfg):
    import local_stt
    threads = int(cfg.get("local_stt_num_threads", local_stt.DEFAULT_THREADS))
    return local_stt.get_recognizer(num_threads=threads).transcribe(audio)
