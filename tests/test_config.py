import json

import core


def test_defaults_without_file(cfg_dir):
    assert core.load_config() == core.DEFAULT_CONFIG


def test_load_returns_a_copy(cfg_dir):
    cfg = core.load_config()
    cfg["key"] = "f1"
    assert core.DEFAULT_CONFIG["key"] == "f9"


def test_save_and_load_roundtrip(cfg_dir):
    cfg = core.load_config()
    cfg.update(mode="toggle", key="f10", threshold=1.5, local_stt_num_threads=2,
               input_device="mic-a", paste_shortcut="ctrl+shift+v")
    core.save_config(cfg)
    assert core.load_config() == cfg


def test_partial_file_is_merged_with_defaults(cfg_dir):
    (cfg_dir / "config.json").write_text(json.dumps({"key": "f5"}))
    cfg = core.load_config()
    assert cfg["key"] == "f5"
    assert cfg["mode"] == core.DEFAULT_CONFIG["mode"]


def test_unknown_keys_from_older_versions_are_ignored(cfg_dir):
    (cfg_dir / "config.json").write_text(json.dumps({"key": "f5", "api_key": "secret"}))
    assert "api_key" not in core.load_config()


def test_corrupt_file_falls_back_to_defaults(cfg_dir):
    (cfg_dir / "config.json").write_text("{not json")
    assert core.load_config() == core.DEFAULT_CONFIG


def test_save_creates_missing_directory(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "dictate"
    monkeypatch.setattr(core, "CONFIG_DIR", target)
    monkeypatch.setattr(core, "CONFIG_PATH", target / "config.json")
    core.save_config(core.load_config())
    assert (target / "config.json").exists()


def test_config_holds_no_dictation_content():
    assert set(core.DEFAULT_CONFIG) == {
        "mode", "key", "threshold", "local_stt_num_threads", "input_device", "paste_shortcut"}


def test_default_hotkey_is_defined():
    assert core.DEFAULT_CONFIG["key"] in core.KEY_MAP
    assert all(isinstance(v, set) and v for v in core.KEY_MAP.values())


def test_mouse_hotkeys_use_the_sentinels_of_the_evdev_listener():
    assert core.KEY_MAP["mouse_back"] == {("mouse", "back")}
    assert core.KEY_MAP["mouse_task"] == {("mouse", "task")}
