"""La carátula en Windows: Spotify cambia el título antes que la miniatura."""

import asyncio
import math
import types

from akp03.media.windows import ArtTracker, WindowsMediaBackend


def test_tracker_rereads_while_settling_then_rarely():
    t = ArtTracker()
    assert t.needs_read("A", 0.0)
    t.store("A", 0.0, b"vieja")
    assert t.needs_read("A", 1.0)  # recién cambiada: se vuelve a leer
    t.store("A", 1.0, b"nueva")
    assert t.current("A")[0] == b"nueva"
    t.store("A", 9.0, b"nueva")
    assert not t.needs_read("A", 12.0)  # ya estable: sólo cada 15 s
    assert t.needs_read("A", 24.5)


def test_art_key_changes_when_image_changes():
    t = ArtTracker()
    t.store("A", 0.0, b"vieja")
    k1 = t.current("A")[1]
    t.store("A", 1.0, b"nueva")
    assert t.current("A")[1] != k1
    assert t.current("sin-imagen") == (None, "sin-imagen")


def test_tracker_forgets_old_tracks():
    t = ArtTracker()
    for i in range(50):
        t.store(f"k{i}", float(i), b"x")
    assert len(t._art) <= ArtTracker.MAX_TRACKS + 1


class FakeSession:
    source_app_user_model_id = "Spotify.exe"

    def __init__(self):
        self.title, self.thumb = "Canción 1", b"portada-1"

    def get_playback_info(self):
        return types.SimpleNamespace(playback_status=4)

    async def try_get_media_properties_async(self):
        return types.SimpleNamespace(title=self.title, artist="Artista", album_artist="",
                                     album_title="", thumbnail=self.thumb)

    def get_timeline_properties(self):
        raise RuntimeError("sin línea de tiempo")


def _backend(session, clock):
    b = WindowsMediaBackend.__new__(WindowsMediaBackend)  # sin hilo WinRT real
    b.prefer_playing = True
    b._session = None
    b._app_stems = set()
    b._art = ArtTracker()
    b._volume = None
    b._volume_retry_at = math.inf
    b._manager = types.SimpleNamespace(get_sessions=lambda: [session],
                                       get_current_session=lambda: session)

    async def read(ref):
        return ref  # la "miniatura" ya son los bytes

    b._read_thumbnail = read
    return b


def test_spotify_late_thumbnail_is_picked_up(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("akp03.media.windows.time.monotonic", lambda: now[0])
    s = FakeSession()
    b = _backend(s, now)

    def poll():
        return asyncio.run(b._get_state_async())

    assert poll().art == b"portada-1"
    # Cambia la canción, pero Windows aún entrega la miniatura vieja…
    s.title = "Canción 2"
    now[0] += 1
    first = poll()
    assert first.title == "Canción 2" and first.art == b"portada-1"
    # …y un segundo después llega la buena: hay que mostrarla.
    s.thumb = b"portada-2"
    now[0] += 1
    second = poll()
    assert second.art == b"portada-2"
    assert second.art_key != first.art_key  # las teclas se redibujan
    # Una tercera canción también cambia (no se queda con la primera).
    s.title, s.thumb = "Canción 3", b"portada-3"
    now[0] += 1
    assert poll().art == b"portada-3"
