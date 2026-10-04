from akp03.device import (DEFAULT_INPUT_MAP, InputEvent, build_input_map, find_candidates,
                          parse_report)
from conftest import make_report


def test_command_packets_are_padded_with_report_id(fake_device, fake_hid):
    fake_device.set_brightness(55)
    pkt = fake_hid.writes[-1]
    assert len(pkt) == 513
    assert pkt[0] == 0
    assert pkt[1:11] == b"CRT\x00\x00LIG\x00\x00"
    assert pkt[11] == 55
    assert set(pkt[12:]) == {0}


def test_brightness_is_clamped(fake_device, fake_hid):
    fake_device.set_brightness(250)
    assert fake_hid.writes[-1][11] == 100


def test_set_key_image_sends_header_and_chunks(fake_device, fake_hid):
    jpeg = bytes(range(256)) * 5  # 1280 bytes -> 3 paquetes de 512
    fake_device.set_key_image(4, jpeg)
    header, *chunks = fake_hid.writes
    assert header[1:9] == b"CRT\x00\x00BAT"
    assert int.from_bytes(header[9:13], "big") == len(jpeg)
    assert header[13] == 4
    assert len(chunks) == 3
    assert all(len(c) == 513 and c[0] == 0 for c in chunks)
    assert b"".join(c[1:] for c in chunks)[:len(jpeg)] == jpeg


def test_initialize_sequence(fake_device, fake_hid):
    fake_device.initialize(70)
    cmds = [w[6:9] for w in fake_hid.writes]
    assert cmds == [b"DIS", b"LIG", b"CLE", b"STP"]
    assert fake_hid.writes[2][12] == 0xFF  # borrar todas


def test_close_blanks_and_closes(fake_device, fake_hid):
    fake_device.close()
    assert fake_hid.closed
    assert [w[6:9] for w in fake_hid.writes] == [b"CLE", b"STP"]


def test_parse_keys_and_buttons():
    assert parse_report(make_report(0x03, 1)) == InputEvent("key", 2, "press", 0, 0x03)
    assert parse_report(make_report(0x03, 0)) == InputEvent("key", 2, "release", 0, 0x03)
    assert parse_report(make_report(0x30, 1)) == InputEvent("button", 1, "press", 0, 0x30)


def test_parse_knobs():
    assert parse_report(make_report(0x91)) == InputEvent("knob", 0, "turn", 1, 0x91)
    assert parse_report(make_report(0x50)) == InputEvent("knob", 1, "turn", -1, 0x50)
    assert parse_report(make_report(0x34)) == InputEvent("knob", 2, "press", 0, 0x34)


def test_parse_with_report_id_prefix_and_unknown_codes():
    assert parse_report(b"\x00" + make_report(0x01)).index == 0
    ev = parse_report(make_report(0x77))
    assert ev.control == "unknown" and ev.code == 0x77
    assert parse_report(b"") is None
    assert parse_report(make_report(0x00)) is None


def test_input_map_overrides():
    mapping = build_input_map({"0x77": "key1", "0x25": "", "bad": "key2", "0x10": "nonsense"})
    assert mapping[0x77] == "key1"
    assert 0x25 not in mapping
    assert 0x10 not in mapping
    assert parse_report(make_report(0x77), mapping).control == "key"
    assert len(DEFAULT_INPUT_MAP) == 18


def test_find_candidates_prefers_known_ids():
    infos = [
        {"vendor_id": 0x046D, "product_id": 0xC52B, "product_string": "Mouse", "path": b"m"},
        {"vendor_id": 0x0300, "product_id": 0x1001, "product_string": "AKP03", "interface_number": 1, "path": b"a1"},
        {"vendor_id": 0x0300, "product_id": 0x1001, "product_string": "AKP03", "interface_number": 0, "path": b"a0"},
    ]
    found = find_candidates(infos)
    assert [i["path"] for i in found] == [b"a0", b"a1"]
    assert find_candidates(infos, vid=0x046D)[0]["path"] == b"m"
    assert find_candidates(infos, vid=0x1234) == []
