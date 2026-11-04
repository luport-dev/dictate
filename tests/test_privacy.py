"""Guards for the promises in docs/DATENSCHUTZ.md. If one of these fails, the
documentation has to change too (or the code change has to be reverted)."""
import ast
import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest

import core
from conftest import ROOT

SRC = ROOT / "src"
SOURCES = sorted(SRC.glob("*.py"))
NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx",
                   "aiohttp", "websocket", "websockets", "ftplib", "smtplib", "telnetlib",
                   "xmlrpc"}


def imports(path):
    """(module, inside_function) for every import in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def visit(node, in_func):
        for child in ast.iter_child_nodes(node):
            inner = in_func or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if isinstance(child, ast.Import):
                found.extend((a.name.split(".")[0], in_func) for a in child.names)
            elif isinstance(child, ast.ImportFrom) and child.module:
                found.append((child.module.split(".")[0], in_func))
            visit(child, inner)

    visit(tree, False)
    return found


def test_sources_exist():
    assert {p.name for p in SOURCES} >= {"core.py", "gui.py", "local_stt.py", "evdev_listener.py"}


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_network_libraries_are_imported(path):
    used = {m for m, _ in imports(path)} & NETWORK_MODULES
    assert not used, f"{path.name} imports network modules: {used}"


def test_only_the_model_download_uses_huggingface():
    users = {p.name: [infunc for m, infunc in imports(p) if m == "huggingface_hub"]
             for p in SOURCES}
    assert {k: v for k, v in users.items() if v} == {"local_stt.py": [True]}


def test_huggingface_telemetry_is_disabled_on_import():
    env = {k: v for k, v in __import__("os").environ.items() if k != "HF_HUB_DISABLE_TELEMETRY"}
    out = subprocess.run(
        [sys.executable, "-c",
         "import os, sys; sys.path.insert(0, r'%s'); import core; "
         "print(os.environ.get('HF_HUB_DISABLE_TELEMETRY'))" % SRC],
        capture_output=True, text=True, env=env, timeout=60)
    assert out.stdout.strip() == "1", out.stderr


def test_no_audio_is_written_to_disk():
    for p in SOURCES:
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"\bwave\b\s*\.\s*open|\.wav\b|import soundfile|sf\.write|write_bytes", text), p.name
    assert not hasattr(core, "keep_failed_audio")
    assert not hasattr(core, "to_wav_bytes")
    assert not hasattr(core, "FAILED_DIR")


def test_no_usage_logging_in_the_code():
    for p in SOURCES:
        assert not re.search(r"log\.(info|debug)\(", p.read_text(encoding="utf-8")), p.name


def test_last_pressed_key_is_not_remembered():
    assert "_last_other" not in (SRC / "gui.py").read_text(encoding="utf-8")


@pytest.fixture
def clean_logger():
    saved = (list(core.log.handlers), core.log.level)
    core.log.handlers.clear()
    yield core.log
    for h in list(core.log.handlers):
        h.close()
    core.log.handlers[:] = saved[0]
    core.log.setLevel(saved[1])


def test_log_records_only_warnings_and_errors(tmp_path, monkeypatch, clean_logger):
    monkeypatch.setattr(core, "LOG_PATH", tmp_path / "dictate.log")
    core.setup_logging()
    assert core.log.level == logging.WARNING
    core.log.info("transcribed 3.2s audio, 41 chars")
    core.log.warning("mic problem")
    for h in core.log.handlers:
        h.flush()
    content = (tmp_path / "dictate.log").read_text(encoding="utf-8")
    assert "mic problem" in content
    assert "transcribed" not in content


def test_log_is_size_limited(tmp_path, monkeypatch, clean_logger):
    monkeypatch.setattr(core, "LOG_PATH", tmp_path / "dictate.log")
    core.setup_logging()
    handler = core.log.handlers[0]
    assert handler.maxBytes <= 1_000_000 and handler.backupCount <= 2


# --- documentation stays in sync ---------------------------------------------

def test_privacy_doc_names_the_current_version():
    import version
    doc = (ROOT / "docs" / "DATENSCHUTZ.md").read_text(encoding="utf-8")
    assert f"Stand: Version {version.__version__}" in doc


def test_privacy_doc_names_the_pinned_model_revision():
    import local_stt
    doc = (ROOT / "docs" / "DATENSCHUTZ.md").read_text(encoding="utf-8")
    assert local_stt.MODEL_REVISION in doc
