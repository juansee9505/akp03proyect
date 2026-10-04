import io

from PIL import Image

from akp03.config import normalize
from akp03.media.base import MediaState, pretty_app_name
from akp03.media.demo import DemoBackend
from akp03.render import AnimatedImage, Overlay, Renderer, encode_key, fmt_time


def _gif(path, colors):
    frames = [Image.new("RGB", (40, 30), c) for c in colors]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    return str(path)


def test_render_default_layout():
    r = Renderer(normalize({}))
    state = DemoBackend().get_state()
    imgs = r.render(state, now=100.0)
    assert len(imgs) == 6
    assert all(i.size == (60, 60) and i.mode == "RGB" for i in imgs)


def test_render_without_media_and_all_types():
    types = ["volume", "volume_up", "volume_down", "mute", "clock", "mic"]
    cfg = normalize({"keys": [{"type": t} for t in types]})
    imgs = Renderer(cfg).render(MediaState(volume=30, muted=True))
    assert len(imgs) == 6


def test_now_playing_panel_spans_keys_and_scrolls():
    cfg = normalize({})
    r = Renderer(cfg)
    state = MediaState(title="Un título muy largo que no cabe en dos teclas del AKP03",
                       artist="Artista", app="Spotify", status="playing",
                       position=10, duration=100)
    a = r.render(state, now=0.0)
    b = r.render(state, now=5.0)
    assert a[1].tobytes() != b[1].tobytes()  # el texto se movió
    assert a[0].tobytes() == b[0].tobytes()  # sin carátula: icono fijo


def test_volume_overlay_changes_panel():
    r = Renderer(normalize({}))
    state = MediaState(title="x", artist="y", volume=50, status="playing")
    normal = r.render(state, now=1.0)
    overlay = r.render(state, now=1.0, overlay=Overlay("volume", "50%", "Spotify", 0.5))
    assert normal[1].tobytes() != overlay[1].tobytes()


def test_overlay_long_text_and_without_panel():
    ov = Overlay("scene", "Una escena con un nombre larguísimo de verdad", "Escena", alert=True)
    assert len(Renderer(normalize({})).render(MediaState(), overlay=ov)) == 6
    cfg = normalize({"keys": [{"type": "cover"}, {"type": "mic"}, {"type": "none"}]})
    r = Renderer(cfg)
    plain = r.render(MediaState(mic_muted=True))
    with_ov = r.render(MediaState(mic_muted=True), overlay=ov)
    assert plain[0].tobytes() != with_ov[0].tobytes()  # sin panel, el aviso va en la carátula
    assert plain[1].getpixel((30, 20))[0] > plain[1].getpixel((30, 20))[1]  # mic silenciado en rojo


def test_custom_gif_animates(tmp_path):
    path = _gif(tmp_path / "a.gif", [(255, 0, 0), (0, 0, 255)])
    cfg = normalize({"keys": [{"type": "image", "image": path}]})
    r = Renderer(cfg)
    f0 = r.render(MediaState(), now=0.05)[0]
    f1 = r.render(MediaState(), now=0.15)[0]
    assert f0.getpixel((30, 30))[0] > 200
    assert f1.getpixel((30, 30))[2] > 200


def test_missing_custom_image_does_not_crash():
    cfg = normalize({"keys": [{"type": "image", "image": "/no/existe.gif"},
                              {"type": "play_pause", "image": "/no/existe.png"}]})
    assert len(Renderer(cfg).render(MediaState())) == 6


def test_cover_uses_art_and_dims_when_paused():
    art = io.BytesIO()
    Image.new("RGB", (100, 100), (250, 250, 250)).save(art, "PNG")
    r = Renderer(normalize({}))
    playing = MediaState(title="t", status="playing", art=art.getvalue(), art_key="k")
    paused = playing.copy(status="paused")
    assert r.render(playing)[0].getpixel((2, 2))[0] > 240
    assert r.render(paused)[0].getpixel((2, 2))[0] < 150


def test_animated_image_frame_index():
    anim = AnimatedImage([Image.new("RGBA", (2, 2))] * 3, [0.1, 0.2, 0.3])
    assert [anim.frame_index(t) for t in (0.0, 0.15, 0.35, 0.65)] == [0, 1, 2, 0]


def test_encode_key_rotation_and_jpeg():
    img = Image.new("RGB", (60, 60))
    img.paste((255, 0, 0), (0, 0, 60, 10))  # franja roja arriba
    data = encode_key(img, rotation=90)
    assert data[:2] == b"\xff\xd8"
    out = Image.open(io.BytesIO(data))
    assert out.size == (60, 60)
    assert out.getpixel((55, 30))[0] > 200  # tras girar 90° a la derecha queda a la derecha


def test_identify_render():
    imgs = Renderer(normalize({})).render_identify()
    assert len(imgs) == 6


def test_helpers():
    assert fmt_time(65) == "1:05"
    assert fmt_time(3725) == "1:02:05"
    assert pretty_app_name("Spotify.exe") == "Spotify"
    assert pretty_app_name("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify") == "Spotify"
    assert pretty_app_name("MSEdge") == "Edge"
    assert pretty_app_name("chrome") == "Chrome"
    assert pretty_app_name("308046B0AF4A39CB") == "Firefox"
    assert pretty_app_name("foobar2000.exe") == "Foobar2000"


def test_position_interpolation():
    st = MediaState(status="playing", position=10.0, duration=12.0, sampled_at=100.0)
    assert st.position_at(101.0) == 11.0
    assert st.position_at(200.0) == 12.0
    assert st.copy(status="paused").position_at(200.0) == 10.0
