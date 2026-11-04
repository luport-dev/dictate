"""Shared fixtures. Tests never touch the real config, microphone, model or
network, and Qt runs headless."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    """Point core's config at a temporary directory."""
    import core
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
