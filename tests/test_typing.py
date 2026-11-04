import subprocess
import time
from types import SimpleNamespace

import pytest

import core


class Runner:
    """Records subprocess.run calls; per-tool exit codes / missing tools."""

    def __init__(self, returncodes=None, missing=(), stdout=b"OLD"):
        self.calls = []
        self.returncodes = returncodes or {}
        self.missing = set(missing)
        self.stdout = stdout

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if cmd[0] in self.missing:
            raise FileNotFoundError(cmd[0])
        return SimpleNamespace(returncode=self.returncodes.get(cmd[0], 0),
                               stdout=self.stdout, stderr="")

    def tools(self):
        return [c[0][0] for c in self.calls]


@pytest.fixture
def run(monkeypatch):
    r = Runner()
    monkeypatch.setattr(subprocess, "run", r)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return r


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(core, "IS_LINUX", True)


def test_empty_text_types_nothing(run, linux):
    core.type_text("")
    assert run.calls == []


def test_windows_types_through_pynput(monkeypatch, run):
    typed = []
    monkeypatch.setattr(core, "IS_LINUX", False)
    monkeypatch.setattr(core.keyboard, "Controller",
                        lambda: SimpleNamespace(type=typed.append))
    core.type_text("Grüße")
    assert typed == ["Grüße"]
    assert run.calls == []   # no clipboard, no external tool


def test_x11_uses_xdotool_and_not_the_clipboard(monkeypatch, run, linux):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    core.type_text("hallo")
    assert run.calls[0][0] == ["xdotool", "type", "--delay", "0", "--", "hallo"]
    assert "wl-copy" not in run.tools()


def test_text_starting_with_a_dash_is_not_taken_as_an_option(monkeypatch, run, linux):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    core.type_text("--help")
    cmd = run.calls[0][0]
    assert cmd.index("--") < cmd.index("--help")


def test_x11_falls_back_to_ydotool(monkeypatch, linux):
    r = Runner(missing={"xdotool"})
    monkeypatch.setattr(subprocess, "run", r)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    core.type_text("hallo")
    assert r.tools() == ["xdotool", "ydotool"]


def test_failed_typing_notifies(monkeypatch, linux):
    r = Runner(returncodes={"xdotool": 1})
    msgs = []
    monkeypatch.setattr(subprocess, "run", r)
    monkeypatch.setattr(core, "notify", lambda msg, **kw: msgs.append(msg))
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    core.type_text("hallo")
    assert msgs and "Typing failed" in msgs[0]


def test_wayland_pastes_and_does_not_type(monkeypatch, run, linux):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    core.type_text("hallo")
    assert "xdotool" not in run.tools()
    assert run.tools().count("ydotool") == 1   # one "key" call, not "type"


def test_wayland_falls_back_to_typing_when_paste_fails(monkeypatch, linux):
    r = Runner(returncodes={"ydotool": 0}, missing={"wl-paste"})
    monkeypatch.setattr(subprocess, "run", r)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    core.type_text("hallo")
    assert ["ydotool", "type", "--key-delay", "0", "--", "hallo"] in [c[0] for c in r.calls]


def events(run):
    key_call = next(c[0] for c in run.calls if c[0][:2] == ["ydotool", "key"])
    return key_call[2:]


def test_paste_ctrl_v(run):
    assert core._paste_wayland("x", "ctrl+v") is True
    assert events(run) == ["29:1", "47:1", "47:0", "29:0"]


def test_paste_ctrl_shift_v_for_terminals(run):
    assert core._paste_wayland("x", "ctrl+shift+v") is True
    assert events(run) == ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]


def test_paste_puts_the_text_on_the_clipboard_then_restores_the_old_one(run):
    core._paste_wayland("secret")
    copies = [c for c in run.calls if c[0] == ["wl-copy"]]
    assert copies[0][1]["input"] == b"secret"
    assert copies[-1][1]["input"] == b"OLD"


def test_paste_clears_clipboard_when_it_was_empty(monkeypatch):
    r = Runner(returncodes={"wl-paste": 1})
    monkeypatch.setattr(subprocess, "run", r)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    core._paste_wayland("secret")
    assert ["wl-copy", "--clear"] in [c[0] for c in r.calls]


def test_paste_reports_failure_when_ydotool_fails(monkeypatch):
    r = Runner(returncodes={"ydotool": 1})
    monkeypatch.setattr(subprocess, "run", r)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert core._paste_wayland("x") is False


def test_paste_reports_failure_when_tools_are_missing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", Runner(missing={"wl-paste"}))
    assert core._paste_wayland("x") is False


def test_notify_uses_the_registered_notifier(monkeypatch):
    got = []
    monkeypatch.setattr(core, "notifier", lambda *a: got.append(a))
    core.notify("hi", "critical", 5000)
    assert got == [("hi", "critical", 5000)]


def test_notify_falls_back_to_notify_send(monkeypatch, run):
    monkeypatch.setattr(core, "notifier", None)
    core.notify("hi", "low", 1000)
    assert run.calls[0][0] == ["notify-send", "-u", "low", "-t", "1000", "Dictate", "hi"]


def test_notify_without_notify_send_is_silent(monkeypatch):
    monkeypatch.setattr(core, "notifier", None)
    monkeypatch.setattr(subprocess, "run", Runner(missing={"notify-send"}))
    core.notify("hi")   # must not raise
