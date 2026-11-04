import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6")

import core  # noqa: E402
import gui  # noqa: E402
from version import __version__  # noqa: E402
from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QDialog, QLabel, QSystemTrayIcon  # noqa: E402


# --- tray icon animation ------------------------------------------------------

def test_bar_motion_stays_within_the_grid_and_moves_one_row():
    motion = gui.BarMotion(4)
    prev = motion.step()
    for _ in range(200):
        cur = motion.step()
        assert len(cur) == 4
        assert all(1 <= h <= gui.SEGMENT_SLOTS for h in cur)
        assert all(abs(a - b) == 1 for a, b in zip(prev, cur))
        prev = cur


@pytest.mark.parametrize("state", ["idle", "recording", "transcribing", "typing"])
def test_state_icons_render(qapp, state):
    icon = gui.make_state_icon(state)
    assert isinstance(icon, QIcon) and not icon.isNull()


def test_every_state_has_a_gradient_with_a_colour_per_segment():
    for grad in gui._GRADIENTS.values():
        assert len(grad) == gui.SEGMENT_SLOTS
    assert max(gui.IDLE_SEGMENTS) <= gui.SEGMENT_SLOTS


# --- settings dialog ------------------------------------------------------------

@pytest.fixture
def mics(monkeypatch):
    monkeypatch.setattr(core, "list_input_devices", lambda: [("Mic A", "mic-a"), ("Mic B", "mic-b")])


def test_dialog_shows_the_version(qapp, mics):
    dlg = gui.SettingsDialog(dict(core.DEFAULT_CONFIG))
    assert dlg.windowTitle() == f"Dictate {__version__}"
    assert any(l.text() == f"Version {__version__}" for l in dlg.findChildren(QLabel))


def test_dialog_values_cover_every_config_key(qapp, mics):
    dlg = gui.SettingsDialog(dict(core.DEFAULT_CONFIG))
    assert set(dlg.values()) == set(core.DEFAULT_CONFIG)


def test_dialog_roundtrips_the_configuration(qapp, mics):
    cfg = dict(core.DEFAULT_CONFIG, mode="toggle", key="f10", threshold=1.5,
               input_device="mic-b", paste_shortcut="ctrl+shift+v")
    values = gui.SettingsDialog(cfg).values()
    assert values["mode"] == "toggle" and values["key"] == "f10"
    assert values["threshold"] == pytest.approx(1.5)
    assert values["input_device"] == "mic-b"
    assert values["paste_shortcut"] == "ctrl+shift+v"


def test_unplugged_microphone_stays_selectable(qapp, mics):
    dlg = gui.SettingsDialog(dict(core.DEFAULT_CONFIG, input_device="gone"))
    assert dlg.values()["input_device"] == "gone"
    assert "not connected" in dlg.mic_box.currentText()


def test_unknown_saved_values_fall_back_to_the_first_entry(qapp, mics):
    dlg = gui.SettingsDialog(dict(core.DEFAULT_CONFIG, mode="bogus", key="bogus"))
    assert dlg.values()["mode"] == "ptt"


def test_paste_option_is_only_offered_on_wayland(qapp, mics, monkeypatch):
    monkeypatch.setattr(core, "IS_LINUX", True)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert gui.SettingsDialog(dict(core.DEFAULT_CONFIG)).paste_box.parent() is not None

    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert gui.SettingsDialog(dict(core.DEFAULT_CONFIG)).paste_box.parent() is None

    monkeypatch.setattr(core, "IS_LINUX", False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert gui.SettingsDialog(dict(core.DEFAULT_CONFIG)).paste_box.parent() is None


def test_mouse_hotkeys_are_only_offered_on_linux(qapp, mics, monkeypatch):
    monkeypatch.setattr(core, "IS_LINUX", False)
    dlg = gui.SettingsDialog(dict(core.DEFAULT_CONFIG))
    keys = [dlg.key_box.itemData(i) for i in range(dlg.key_box.count())]
    assert not any(k.startswith("mouse_") for k in keys) and "f9" in keys


def test_model_status_reflects_installation(qapp, mics, monkeypatch):
    import local_stt
    monkeypatch.setattr(local_stt, "model_installed", lambda: True)
    assert "Installed" in gui.SettingsDialog(dict(core.DEFAULT_CONFIG)).local_status_label.text()
    monkeypatch.setattr(local_stt, "model_installed", lambda: False)
    dlg = gui.SettingsDialog(dict(core.DEFAULT_CONFIG))
    assert dlg.local_status_label.text() == "Not installed."
    assert "Download" in dlg.local_download_btn.text()


# --- main window -----------------------------------------------------------------

@pytest.fixture
def window(qapp, monkeypatch, cfg_dir):
    """A real MainWindow with a tray, but no hotkey hook, mic, model or dialog."""
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(gui, "_make_listener", lambda *a: MagicMock())
    monkeypatch.setattr(core, "model_ready", lambda: True)
    monkeypatch.setattr(core, "notifier", None)
    monkeypatch.setattr(gui.MainWindow, "_preload_local_if_needed", lambda self: None)
    win = gui.MainWindow()
    yield win
    win._anim_timer.stop()
    win.close()


def test_tray_menu_offers_settings_restart_and_quit(window):
    menu = window.tray.contextMenu()
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == ["Settings", "Restart", "Quit"]


def test_window_is_never_shown(window):
    assert not window.isVisible()


def test_tooltip_carries_the_version(window):
    assert __version__ in window.tray.toolTip()


def test_status_changes_update_the_tooltip_and_animate(window):
    window.set_status("recording", "Recording...")
    assert "Recording..." in window.tray.toolTip()
    assert window._anim_timer.isActive()
    window.set_status("ready", "Ready")
    assert not window._anim_timer.isActive()


def test_left_click_shows_the_menu():
    fake = SimpleNamespace(tray=MagicMock())
    gui.MainWindow.on_tray_activated(fake, QSystemTrayIcon.ActivationReason.Trigger)
    fake.tray.contextMenu.return_value.popup.assert_called_once()


@pytest.mark.parametrize("reason", [QSystemTrayIcon.ActivationReason.DoubleClick,
                                    QSystemTrayIcon.ActivationReason.MiddleClick,
                                    QSystemTrayIcon.ActivationReason.Context])
def test_other_clicks_do_not_pop_up_a_second_menu(reason):
    fake = SimpleNamespace(tray=MagicMock())
    gui.MainWindow.on_tray_activated(fake, reason)
    fake.tray.contextMenu.return_value.popup.assert_not_called()


def test_first_run_prompts_for_the_model_download(qapp, monkeypatch, cfg_dir):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    monkeypatch.setattr(gui, "_make_listener", lambda *a: MagicMock())
    monkeypatch.setattr(core, "model_ready", lambda: False)
    monkeypatch.setattr(gui.MainWindow, "_preload_local_if_needed", lambda self: None)
    scheduled = []
    monkeypatch.setattr(gui.QTimer, "singleShot", staticmethod(lambda ms, fn: scheduled.append(fn)))
    win = gui.MainWindow()
    assert any(getattr(f, "__name__", "") == "first_run" for f in scheduled)


# --- settings flow -----------------------------------------------------------------

def test_saving_settings_persists_and_restarts_the_listener(monkeypatch):
    saved = []
    monkeypatch.setattr(core, "save_config", saved.append)
    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    dialog.values.return_value = {"key": "f10"}
    monkeypatch.setattr(gui, "SettingsDialog", lambda cfg, parent: dialog)
    fake = SimpleNamespace(cfg={"key": "f9", "mode": "ptt"}, restart_listener=MagicMock(),
                           _preload_local_if_needed=MagicMock())
    gui.MainWindow.open_settings(fake)
    assert fake.cfg["key"] == "f10" and saved == [fake.cfg]
    fake.restart_listener.assert_called_once()
    fake._preload_local_if_needed.assert_called_once()


def test_cancelling_settings_changes_nothing(monkeypatch):
    saved = []
    monkeypatch.setattr(core, "save_config", saved.append)
    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Rejected
    monkeypatch.setattr(gui, "SettingsDialog", lambda cfg, parent: dialog)
    fake = SimpleNamespace(cfg={"key": "f9"}, restart_listener=MagicMock(),
                           _preload_local_if_needed=MagicMock())
    gui.MainWindow.open_settings(fake)
    assert fake.cfg == {"key": "f9"} and saved == []
    fake.restart_listener.assert_not_called()


# --- hotkey logic (no real keyboard hook) ----------------------------------------------

def make_fake(mode="ptt", threshold=0.0, model=True):
    fake = SimpleNamespace(
        cfg={"mode": mode, "threshold": threshold, "key": "f9", "input_device": ""},
        key_held=False, combo=False, recording_active=False, press_time=0.0,
        start_recording=MagicMock(return_value=True),
        stop_and_transcribe=MagicMock(), _discard_take=MagicMock(),
        set_status=MagicMock(), bridge=MagicMock(), _target_keys=set(), listener=None)
    return fake


def test_ptt_press_starts_and_release_stops():
    fake = make_fake("ptt")
    gui.MainWindow.on_press(fake)
    assert fake.recording_active and fake.key_held
    gui.MainWindow.on_release(fake)
    fake.stop_and_transcribe.assert_called_once()
    assert not fake.recording_active and not fake.key_held


def test_ptt_press_is_ignored_while_held():
    fake = make_fake("ptt")
    gui.MainWindow.on_press(fake)
    gui.MainWindow.on_press(fake)
    fake.start_recording.assert_called_once()


def test_ptt_release_before_the_minimum_hold_discards():
    fake = make_fake("ptt", threshold=5.0)
    gui.MainWindow.on_press(fake)
    gui.MainWindow.on_release(fake)
    fake._discard_take.assert_called_once()
    fake.stop_and_transcribe.assert_not_called()


def test_failed_start_does_not_mark_recording():
    fake = make_fake("ptt")
    fake.start_recording.return_value = False
    gui.MainWindow.on_press(fake)
    assert not fake.recording_active


def test_toggle_mode_starts_then_stops_on_the_next_tap():
    fake = make_fake("toggle")
    gui.MainWindow.on_press(fake)
    gui.MainWindow.on_release(fake)
    assert fake.recording_active
    fake.stop_and_transcribe.assert_not_called()
    gui.MainWindow.on_press(fake)
    fake.stop_and_transcribe.assert_called_once()
    assert not fake.recording_active


def test_another_key_while_holding_cancels_a_ptt_recording():
    fake = make_fake("ptt")
    gui.MainWindow.on_press(fake)
    gui.MainWindow.on_other_press(fake)
    fake._discard_take.assert_called_once()
    assert fake.combo and not fake.recording_active


def test_another_key_without_the_hotkey_does_nothing():
    fake = make_fake("ptt")
    gui.MainWindow.on_other_press(fake)
    fake._discard_take.assert_not_called()


def test_another_key_does_not_kill_a_toggle_session():
    fake = make_fake("toggle")
    gui.MainWindow.on_press(fake)
    gui.MainWindow.on_other_press(fake)
    fake._discard_take.assert_not_called()
    assert fake.recording_active


def test_long_cancelled_hold_is_reported(monkeypatch):
    msgs = []
    monkeypatch.setattr(core, "notify", lambda msg, **kw: msgs.append(msg))
    fake = make_fake("ptt")
    gui.MainWindow.on_press(fake)
    fake.press_time -= 3.0
    gui.MainWindow.on_other_press(fake)
    assert msgs and "cancelled" in msgs[0]


def test_quick_shortcut_cancel_is_silent(monkeypatch):
    msgs = []
    monkeypatch.setattr(core, "notify", lambda msg, **kw: msgs.append(msg))
    fake = make_fake("ptt")
    gui.MainWindow.on_press(fake)
    gui.MainWindow.on_other_press(fake)
    assert msgs == []


def test_listener_maps_keys_to_signals(monkeypatch):
    captured = {}
    monkeypatch.setattr(gui, "_make_listener",
                        lambda press, release: captured.update(press=press, release=release))
    fake = make_fake()
    gui.MainWindow.start_listener(fake)
    f9 = next(iter(core.KEY_MAP["f9"]))
    captured["press"](f9)
    fake.bridge.hotkey_press.emit.assert_called_once()
    captured["press"]("a")
    fake.bridge.other_press.emit.assert_called_once()
    captured["release"](f9)
    fake.bridge.hotkey_release.emit.assert_called_once()
    captured["release"]("a")
    fake.bridge.hotkey_release.emit.assert_called_once()
    assert not hasattr(fake, "_last_other")   # other keys are not remembered


def test_unknown_hotkey_does_not_start_a_listener(monkeypatch):
    called = []
    monkeypatch.setattr(gui, "_make_listener", lambda *a: called.append(a))
    fake = make_fake()
    fake.cfg["key"] = "bogus"
    gui.MainWindow.start_listener(fake)
    assert called == []


# --- dictation pipeline ------------------------------------------------------------------

def pipeline_fake(monkeypatch, audio, text=None, error=None):
    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    monkeypatch.setattr(core.Recorder, "finish", staticmethod(lambda take: audio))
    if error:
        def boom(a, cfg):
            raise error
        monkeypatch.setattr(core, "transcribe", boom)
    else:
        monkeypatch.setattr(core, "transcribe", lambda a, cfg: text)
    return SimpleNamespace(cfg={}, bridge=MagicMock(), recorder=MagicMock())


def loud(n=16000):
    import numpy as np
    return np.full((n, 1), 5000, dtype=np.int16)


def test_transcribed_text_is_emitted(monkeypatch):
    fake = pipeline_fake(monkeypatch, loud(), text="hallo")
    gui.MainWindow._stop_and_transcribe_worker(fake, object())
    fake.bridge.transcribed.emit.assert_called_once_with("hallo")


def test_no_audio_reports_a_microphone_problem(monkeypatch):
    fake = pipeline_fake(monkeypatch, None)
    gui.MainWindow._stop_and_transcribe_worker(fake, object())
    fake.recorder.mark_stale.assert_called_once()
    fake.bridge.mic_error.emit.assert_called_once()


def test_silence_reports_a_microphone_problem(monkeypatch):
    import numpy as np
    fake = pipeline_fake(monkeypatch, np.zeros((16000, 1), dtype=np.int16), text="")
    gui.MainWindow._stop_and_transcribe_worker(fake, object())
    fake.bridge.mic_error.emit.assert_called_once()
    fake.recorder.mark_stale.assert_called_once()


def test_speech_without_result_reports_an_error_and_keeps_no_audio(monkeypatch):
    fake = pipeline_fake(monkeypatch, loud(), text="")
    gui.MainWindow._stop_and_transcribe_worker(fake, object())
    fake.bridge.error.emit.assert_called_once()
    assert "no speech recognised" in fake.bridge.error.emit.call_args[0][0]
    fake.recorder.mark_stale.assert_not_called()
    assert not hasattr(core, "keep_failed_audio")


def test_transcription_failure_is_reported(monkeypatch):
    fake = pipeline_fake(monkeypatch, loud(), error=RuntimeError("model missing"))
    gui.MainWindow._stop_and_transcribe_worker(fake, object())
    fake.bridge.error.emit.assert_called_once_with("model missing")


def test_text_is_typed_with_the_configured_paste_shortcut(monkeypatch):
    typed = []
    monkeypatch.setattr(core, "type_text", lambda text, shortcut: typed.append((text, shortcut)))
    done = MagicMock()
    fake = SimpleNamespace(cfg={"paste_shortcut": "ctrl+shift+v"},
                           bridge=SimpleNamespace(typing_done=done), set_status=MagicMock())
    gui.MainWindow.on_transcribed(fake, "hallo")
    import time
    for _ in range(100):
        if done.emit.called:
            break
        time.sleep(0.02)
    assert typed == [("hallo", "ctrl+shift+v")]
    done.emit.assert_called_once()


def test_empty_text_just_returns_to_ready():
    fake = SimpleNamespace(cfg={}, set_status=MagicMock())
    gui.MainWindow.on_transcribed(fake, "")
    fake.set_status.assert_called_once_with("ready", "Ready")


def test_missing_model_blocks_recording(monkeypatch):
    monkeypatch.setattr(core, "model_ready", lambda: False)
    fake = SimpleNamespace(set_status=MagicMock(), cfg={}, recorder=MagicMock())
    assert gui.MainWindow.start_recording(fake) is False
    fake.recorder.start.assert_not_called()


def test_microphone_error_on_start_is_reported(monkeypatch):
    monkeypatch.setattr(core, "model_ready", lambda: True)
    fake = SimpleNamespace(set_status=MagicMock(), cfg={"input_device": "m"},
                           recorder=MagicMock(), on_mic_error=MagicMock())
    fake.recorder.start.side_effect = OSError("busy")
    assert gui.MainWindow.start_recording(fake) is False
    fake.on_mic_error.assert_called_once()
    assert fake.recorder.device == "m"


# --- single instance --------------------------------------------------------------------------

def test_second_instance_is_refused(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "notify", lambda *a, **k: None)
    monkeypatch.setattr(gui, "_LOCK", None)
    assert gui.acquire_single_instance_lock() is True
    first = gui._LOCK
    assert gui.acquire_single_instance_lock() is False
    assert gui._LOCK is first
    first.unlock()
