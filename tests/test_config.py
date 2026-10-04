import json

from akp03.config import DEFAULT_CONFIG, import_media_file, load_config, normalize, save_config


def test_defaults_are_complete():
    cfg = normalize({})
    assert cfg == normalize(DEFAULT_CONFIG)
    assert len(cfg["keys"]) == 6 and len(cfg["knobs"]) == 3 and len(cfg["buttons"]) == 3


def test_partial_config_is_merged_and_validated():
    cfg = normalize({
        "device": {"brightness": 300, "rotation": 100, "vid": "0x0300"},
        "keys": [{"type": "image", "image": "a.gif"}, {"type": "bogus"}],
        "version": 2,
        "knobs": [{"turn": "seek"}],
        "display": {"fps": 999, "theme_color": 5},
    })
    assert cfg["device"]["brightness"] == 100
    assert cfg["device"]["rotation"] == 90
    assert cfg["device"]["vid"] == "0x0300"
    assert cfg["keys"][0] == {**DEFAULT_CONFIG["keys"][0], "type": "image", "image": "a.gif"}
    assert cfg["keys"][1]["type"] == "none"
    assert cfg["keys"][5]["type"] == "next"
    assert cfg["knobs"][0] == {"turn": "seek", "press": "play_pause", "target": None}
    assert cfg["display"]["fps"] == 30
    assert cfg["display"]["theme_color"] == DEFAULT_CONFIG["display"]["theme_color"]


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "sub" / "config.json"
    cfg = normalize({"volume_step": 5})
    save_config(cfg, path)
    assert load_config(path) == cfg
    assert json.loads(path.read_text(encoding="utf-8"))["volume_step"] == 5


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{no es json", encoding="utf-8")
    assert load_config(path) == normalize({})


def test_import_media_file_copies(tmp_path):
    src = tmp_path / "mi gif.GIF"
    src.write_bytes(b"GIF89a")
    stored = import_media_file(src, base_dir=tmp_path / "app")
    assert stored.endswith(".gif")
    assert (tmp_path / "app" / "media").exists()
    assert open(stored, "rb").read() == b"GIF89a"
    # Volver a importar un archivo que ya está en la carpeta no lo duplica.
    assert import_media_file(stored, base_dir=tmp_path / "app") == stored


def test_migration_from_v1_keeps_customized_controls():
    old_defaults = {
        "version": 1,
        "knobs": [{"turn": "volume", "press": "mute"}, {"turn": "track", "press": "play_pause"},
                  {"turn": "brightness", "press": "none"}],
        "buttons": [{"action": "previous", "target": None}, {"action": "play_pause", "target": None},
                    {"action": "next", "target": None}],
    }
    cfg = normalize(old_defaults)
    assert cfg["version"] == 2
    assert cfg["knobs"][0]["turn"] == "app_volume"
    assert cfg["knobs"][1]["press"] == "mic_mute"
    assert cfg["buttons"][0]["action"] == "obs_record"

    custom = dict(old_defaults, knobs=[{"turn": "seek", "press": "none"}])
    assert normalize(custom)["knobs"][0] == {"turn": "seek", "press": "none", "target": None}
    # Ya en v2: no se toca nada.
    v2 = normalize({"version": 2, "buttons": old_defaults["buttons"]})
    assert v2["buttons"][0]["action"] == "previous"
