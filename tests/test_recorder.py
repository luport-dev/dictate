import numpy as np

import core


class FakeStream:
    def __init__(self, **kw):
        self.kwargs = kw
        self.callback = kw["callback"]
        self.started = self.stopped = self.closed = False
        self.fail_close = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        if self.fail_close:
            raise OSError("device vanished")

    def close(self):
        self.closed = True


class FakeSD:
    def __init__(self):
        self.streams = []
        self.terminated = self.initialised = 0
        self.fail_reinit = False

    def InputStream(self, **kw):
        s = FakeStream(**kw)
        self.streams.append(s)
        return s

    def _terminate(self):
        self.terminated += 1
        if self.fail_reinit:
            raise OSError("no PortAudio")

    def _initialize(self):
        self.initialised += 1


def make(monkeypatch):
    sd = FakeSD()
    monkeypatch.setattr(core, "sd", sd)
    monkeypatch.setattr(core, "_resolve_device", lambda name: None)
    return sd, core.Recorder()


def feed(stream, n=3):
    for _ in range(n):
        stream.callback(np.ones((160, 1), dtype=np.int16), 160, None, None)


def test_start_opens_a_16k_mono_int16_stream(monkeypatch):
    sd, rec = make(monkeypatch)
    rec.start()
    s = sd.streams[0]
    assert s.started and rec.recording
    assert (s.kwargs["samplerate"], s.kwargs["channels"], s.kwargs["dtype"]) == (16000, 1, "int16")


def test_second_start_is_a_noop(monkeypatch):
    sd, rec = make(monkeypatch)
    rec.start()
    rec.start()
    assert len(sd.streams) == 1


def test_stop_returns_concatenated_audio(monkeypatch):
    sd, rec = make(monkeypatch)
    rec.start()
    feed(sd.streams[0], 3)
    audio = rec.stop()
    assert audio.shape == (480, 1)
    assert sd.streams[0].stopped and sd.streams[0].closed
    assert not rec.recording


def test_stop_without_frames_returns_none(monkeypatch):
    _, rec = make(monkeypatch)
    rec.start()
    assert rec.stop() is None


def test_detach_hands_over_the_take_and_allows_a_new_one(monkeypatch):
    sd, rec = make(monkeypatch)
    rec.start()
    take = rec.detach()
    assert take is not None and not rec.recording
    rec.start()
    assert len(sd.streams) == 2


def test_detach_without_take_is_none(monkeypatch):
    _, rec = make(monkeypatch)
    assert rec.detach() is None
    assert core.Recorder.finish(None) is None


def test_audio_survives_a_vanished_microphone(monkeypatch):
    sd, rec = make(monkeypatch)
    rec.start()
    feed(sd.streams[0], 2)
    sd.streams[0].fail_close = True
    assert rec.stop().shape == (320, 1)


def test_stale_recorder_reinitialises_audio_before_next_take(monkeypatch):
    sd, rec = make(monkeypatch)
    rec.mark_stale()
    rec.start()
    assert (sd.terminated, sd.initialised) == (1, 1)
    rec.stop()
    rec.start()
    assert sd.terminated == 1   # not stale any more


def test_reinit_if_idle_only_when_stale_and_idle(monkeypatch):
    sd, rec = make(monkeypatch)
    rec.reinit_if_idle()
    assert sd.terminated == 0
    rec.mark_stale()
    rec.start()          # consumes the stale flag (1 reinit)
    rec.mark_stale()
    rec.reinit_if_idle() # busy recorder is left alone
    assert sd.terminated == 1


def test_reinit_failure_is_swallowed(monkeypatch):
    sd, rec = make(monkeypatch)
    sd.fail_reinit = True
    rec.mark_stale()
    rec.reinit_if_idle()   # must not raise
    assert sd.terminated == 1


def test_configured_microphone_is_resolved(monkeypatch):
    sd, rec = make(monkeypatch)
    monkeypatch.setattr(core, "_resolve_device", lambda name: 7 if name == "Mic" else None)
    rec.device = "Mic"
    rec.start()
    assert sd.streams[0].kwargs["device"] == 7
