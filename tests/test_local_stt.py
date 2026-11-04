import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import local_stt as L

SR = L.TARGET_SR


# --- _split ---------------------------------------------------------------

def test_short_audio_is_one_piece():
    audio = np.zeros(SR * 5, dtype=np.float32)
    pieces = list(L._split(audio))
    assert len(pieces) == 1 and len(pieces[0]) == len(audio)


def test_long_audio_is_cut_into_pieces_without_loss():
    rng = np.random.default_rng(0)
    audio = rng.normal(0, 0.1, SR * 25).astype(np.float32)
    pieces = list(L._split(audio, max_s=10))
    assert all(len(p) <= 10 * SR for p in pieces)
    assert np.array_equal(np.concatenate(pieces), audio)
    assert len(pieces) >= 3


def test_cut_lands_in_the_quietest_spot_near_the_end():
    rng = np.random.default_rng(1)
    audio = rng.normal(0, 0.3, SR * 25).astype(np.float32)
    gap = slice(int(8.0 * SR), int(8.6 * SR))   # inside the search window of piece 1
    audio[gap] = 0.0
    first = next(iter(L._split(audio, max_s=10)))
    assert gap.start <= len(first) <= gap.stop


def test_exact_maximum_length_is_not_split():
    audio = np.ones(10 * SR, dtype=np.float32)
    assert len(list(L._split(audio, max_s=10))) == 1


# --- model files ------------------------------------------------------------

def test_model_installed_needs_every_file(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "MODEL_DIR", tmp_path)
    assert not L.model_installed()
    for f in L.MODEL_FILES[:-1]:
        (tmp_path / f).write_bytes(b"x")
    assert not L.model_installed()
    (tmp_path / L.MODEL_FILES[-1]).write_bytes(b"x")
    assert L.model_installed()


def test_model_revision_is_pinned():
    assert len(L.MODEL_REVISION) == 40 and set(L.MODEL_REVISION) <= set("0123456789abcdef")


def test_download_fetches_the_pinned_revision(tmp_path, monkeypatch):
    calls, messages = [], []
    fake = SimpleNamespace(snapshot_download=lambda **kw: calls.append(kw))
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setattr(L, "MODEL_DIR", tmp_path / "m")
    L.download_model(progress_cb=messages.append)
    assert calls == [dict(repo_id=L.MODEL_REPO, revision=L.MODEL_REVISION,
                          local_dir=str(tmp_path / "m"), allow_patterns=L.MODEL_FILES)]
    assert len(messages) == 2


def test_download_without_huggingface_hub_explains_what_to_do(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(RuntimeError, match="pip install huggingface_hub"):
        L.download_model()


# --- LocalRecognizer (fake sherpa-onnx, no model needed) -------------------------

class FakeSherpa:
    def __init__(self, text_for=lambda wave: "hallo"):
        self.seen = []
        self.text_for = text_for

    def create_stream(self):
        return SimpleNamespace(wave=None, result=SimpleNamespace(text=""))

    def decode_stream(self, s):
        self.seen.append(s.wave)
        s.result.text = f"  {self.text_for(s.wave)} "

    # the stream needs accept_waveform; patch it in create_stream
    def _stream(self):
        outer = self
        s = SimpleNamespace(result=SimpleNamespace(text=""))
        s.accept_waveform = lambda sr, w: setattr(s, "wave", w)
        return s


def make_recognizer(fake=None, load_error=None):
    fake = fake or FakeSherpa()
    fake.create_stream = fake._stream
    rec = object.__new__(L.LocalRecognizer)
    rec.num_threads = 4
    rec._decode_lock = threading.Lock()
    rec._ready = threading.Event()
    rec._ready.set()
    rec._load_error = load_error
    rec._recognizer = None if load_error else fake
    return rec, fake


def test_transcribe_returns_stripped_text():
    rec, _ = make_recognizer()
    assert rec.transcribe(np.ones(SR, dtype=np.int16)) == "hallo"


def test_int16_audio_is_scaled_to_float32():
    rec, fake = make_recognizer()
    rec.transcribe(np.full(SR, 16384, dtype=np.int16))
    wave = fake.seen[0]
    assert wave.dtype == np.float32 and abs(float(wave.max()) - 0.5) < 1e-3


def test_column_audio_is_squeezed():
    rec, fake = make_recognizer()
    rec.transcribe(np.ones((SR, 1), dtype=np.int16))
    assert fake.seen[0].ndim == 1


def test_quiet_audio_is_raised_to_the_target_peak():
    rec, fake = make_recognizer()
    rec.transcribe(np.full(SR, 0.05, dtype=np.float32))
    assert float(np.abs(fake.seen[0]).max()) == pytest.approx(L.TARGET_PEAK, rel=1e-3)


def test_gain_is_capped_so_room_noise_stays_noise():
    rec, fake = make_recognizer()
    rec.transcribe(np.full(SR, 0.001, dtype=np.float32))
    assert float(np.abs(fake.seen[0]).max()) == pytest.approx(0.001 * L.MAX_GAIN, rel=1e-3)


def test_loud_audio_is_not_changed():
    rec, fake = make_recognizer()
    rec.transcribe(np.full(SR, 0.8, dtype=np.float32))
    assert float(np.abs(fake.seen[0]).max()) == pytest.approx(0.8)


def test_digital_silence_does_not_divide_by_zero():
    rec, fake = make_recognizer()
    rec.transcribe(np.zeros(SR, dtype=np.float32))
    assert float(np.abs(fake.seen[0]).max()) == 0.0


def test_empty_long_piece_is_retried_in_shorter_parts():
    fake = FakeSherpa(text_for=lambda w: "" if len(w) > L.RETRY_CHUNK_S * SR else "x")
    rec, _ = make_recognizer(fake)
    text = rec.transcribe(np.full(SR * 45, 0.3, dtype=np.float32))
    assert text.split() and set(text.split()) == {"x"} and len(text.split()) >= 3


def test_empty_short_piece_is_not_retried():
    rec, fake = make_recognizer(FakeSherpa(text_for=lambda w: ""))
    assert rec.transcribe(np.full(SR * 5, 0.3, dtype=np.float32)) == ""
    assert len(fake.seen) == 1


def test_failed_load_is_reported_on_use():
    rec, _ = make_recognizer(load_error=RuntimeError("bad model"))
    assert not rec.is_ready()
    with pytest.raises(RuntimeError, match="bad model"):
        rec.transcribe(np.ones(SR, dtype=np.int16))


# --- singleton ----------------------------------------------------------------

def test_recognizer_is_reused_until_the_thread_count_changes(monkeypatch):
    created = []

    class Fake:
        def __init__(self, n):
            self.num_threads = n
            created.append(n)

    monkeypatch.setattr(L, "LocalRecognizer", Fake)
    monkeypatch.setattr(L, "_instance", None)
    a = L.get_recognizer(4)
    assert L.get_recognizer(4) is a
    b = L.preload(2)
    assert b is not a and created == [4, 2]
