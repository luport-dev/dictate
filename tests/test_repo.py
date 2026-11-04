"""Repository layout, version and installer consistency."""
import re
import subprocess
import sys

import pytest

import version
from conftest import ROOT


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", version.__version__)


def test_version_script_prints_the_version():
    out = subprocess.run([sys.executable, str(ROOT / "src" / "version.py")],
                         capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == version.__version__


def test_readme_names_the_current_version():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Current version: **{version.__version__}**" in readme


@pytest.mark.parametrize("path", [
    "src/gui.py", "src/core.py", "src/local_stt.py", "src/evdev_listener.py", "src/version.py",
    "docs/DATENSCHUTZ.md", "docs/WAYLAND_NOTES.md", "assets/icon.png",
    "scripts/install.sh", "scripts/install.ps1", "README.md", "LICENSE"])
def test_expected_files_exist(path):
    assert (ROOT / path).is_file()


def test_no_application_code_in_the_repo_root():
    assert list(ROOT.glob("*.py")) == []


@pytest.mark.parametrize("script", ["install.sh", "install.ps1"])
def test_installers_point_at_existing_files(script):
    text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    for ref in set(re.findall(r"(?:src|assets)/[\w.]+", text)):
        assert (ROOT / ref).exists(), f"{script} references missing {ref}"


def test_installers_install_the_same_python_packages():
    sh = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    ps = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    for pkg in ["PyQt6", "sounddevice", "pynput", "numpy", "sherpa-onnx", "soundfile", "huggingface_hub"]:
        assert pkg in sh and pkg in ps


def test_installers_resolve_the_repo_root_from_the_scripts_folder():
    assert "/.." in (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "Split-Path $PSScriptRoot -Parent" in (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")


def test_readme_links_point_at_existing_files():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\(((?:docs|assets|src|scripts)/[^)#]+)\)", readme):
        assert (ROOT / target).exists(), target


def test_readme_mentions_the_installer_locations():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scripts/install.sh" in readme and "scripts/install.ps1" in readme


def test_gui_finds_its_icon():
    import gui
    from pathlib import Path
    icon = Path(gui.__file__).resolve().parent.parent / "assets" / "icon.png"
    assert icon.is_file()


def test_generated_icon_is_not_tracked():
    assert "assets/icon.ico" in (ROOT / ".gitignore").read_text(encoding="utf-8")
