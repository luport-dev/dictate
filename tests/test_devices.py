from types import SimpleNamespace

import core


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSD:
    """Stand-in for the sounddevice module."""

    def __init__(self, devices=(), hostapi=0, fail_open=False):
        self._devices = list(devices)
        self.default = SimpleNamespace(hostapi=hostapi)
        self.fail_open = fail_open
        self.callbacks = []

    def query_devices(self):
        return self._devices

    def InputStream(self, **kw):
        if self.fail_open:
            raise OSError("no device")
        self.callbacks.append(kw["callback"])
        return _Ctx()

    def sleep(self, ms):
        for cb in self.callbacks:
            cb(b"x", 1, None, None)


def dev(name, ch=1, hostapi=0):
    return {"name": name, "max_input_channels": ch, "hostapi": hostapi}


def test_friendly_name_on_windows_is_unchanged(monkeypatch):
    monkeypatch.setattr(core, "IS_LINUX", False)
    assert core.friendly_device_name("Microphone (USB)") == "Microphone (USB)"


def test_friendly_name_uses_pipewire_description(monkeypatch):
    monkeypatch.setattr(core, "IS_LINUX", True)
    descs = {"alsa_input.usb-x.analog": "Webcam Mic"}
    assert core.friendly_device_name("alsa_input.usb-x.analog", descs) == "Webcam Mic"


def test_friendly_name_fallback_cleans_the_raw_name(monkeypatch):
    monkeypatch.setattr(core, "IS_LINUX", True)
    name = "alsa_input.usb-Logitech_Webcam-00.analog-stereo"
    assert core.friendly_device_name(name, {}) == "Logitech Webcam 00"


def test_offered_skips_output_only_devices(monkeypatch):
    monkeypatch.setattr(core, "sd", FakeSD())
    assert not core._offered(dev("speaker", ch=0))


def test_offered_on_linux_only_pipewire_sources(monkeypatch):
    monkeypatch.setattr(core, "sd", FakeSD())
    monkeypatch.setattr(core, "IS_LINUX", True)
    assert core._offered(dev("alsa_input.usb-mic"))
    assert not core._offered(dev("hw:1,0"))


def test_offered_on_windows_only_default_host_api(monkeypatch):
    monkeypatch.setattr(core, "sd", FakeSD(hostapi=2))
    monkeypatch.setattr(core, "IS_LINUX", False)
    assert core._offered(dev("Mic", hostapi=2))
    assert not core._offered(dev("Mic", hostapi=0))


def test_resolve_device_empty_name_is_system_default():
    assert core._resolve_device("") is None


def test_resolve_device_finds_index(monkeypatch):
    monkeypatch.setattr(core, "sd", FakeSD([dev("Other"), dev("Mic")]))
    monkeypatch.setattr(core, "IS_LINUX", False)
    assert core._resolve_device("Mic") == 1


def test_resolve_device_unplugged_mic_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(core, "sd", FakeSD([dev("Other")]))
    monkeypatch.setattr(core, "IS_LINUX", False)
    assert core._resolve_device("Gone") is None


def test_list_input_devices(monkeypatch):
    monkeypatch.setattr(core, "sd", FakeSD([dev("Mic"), dev("Speaker", ch=0)]))
    monkeypatch.setattr(core, "IS_LINUX", False)
    assert core.list_input_devices() == [("Mic", "Mic")]


def test_list_input_devices_survives_audio_errors(monkeypatch):
    class Broken(FakeSD):
        def query_devices(self):
            raise OSError("PortAudio not initialised")
    monkeypatch.setattr(core, "sd", Broken())
    assert core.list_input_devices() == []


def test_mic_available_true_when_frames_arrive(monkeypatch):
    monkeypatch.setattr(core, "sd", FakeSD())
    assert core.mic_available() is True


def test_mic_available_false_when_open_fails(monkeypatch):
    monkeypatch.setattr(core, "sd", FakeSD(fail_open=True))
    assert core.mic_available() is False


def test_mic_available_false_when_no_frames(monkeypatch):
    sd = FakeSD()
    sd.sleep = lambda ms: None
    monkeypatch.setattr(core, "sd", sd)
    assert core.mic_available() is False
