"""Lógica principal: une dispositivo, medios y renderizado."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Callable, Optional

from .config import DEFAULT_KEY_ACTION, NUM_KEYS, save_config
from .device import AKP03Device, DeviceError, InputEvent, build_input_map
from .media.base import MediaBackend, MediaState
from .obs import OBSActions, OBSClient, OBSConnectionLost, OBSError
from .render import Overlay, Renderer, encode_key

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 10.0
RECONNECT_SECONDS = 3.0
TRACK_KNOB_DEBOUNCE = 0.35


def _parse_id(value) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def open_from_config(cfg: dict) -> AKP03Device:
    dev_cfg = cfg["device"]
    return AKP03Device.open(
        vid=_parse_id(dev_cfg.get("vid")), pid=_parse_id(dev_cfg.get("pid")),
        packet_size=int(dev_cfg.get("packet_size") or 0),
        input_map=build_input_map(dev_cfg.get("input_map")),
    )


def open_target(target: str) -> None:
    """Abre una URL, archivo o programa (acción "open")."""
    if not target:
        return
    if target.startswith(("http://", "https://")):
        webbrowser.open(target)
    elif sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


class Controller:
    def __init__(self, config: dict, backend: MediaBackend,
                 device_opener: Optional[Callable[[dict], AKP03Device]] = None,
                 config_path=None, use_device: bool = True):
        self.config = config
        self.backend = backend
        self.config_path = config_path
        self.use_device = use_device
        self.renderer = Renderer(config)
        self._device_opener = device_opener or self._default_open

        self.device: Optional[AKP03Device] = None
        self.status = "Buscando AKP03…"
        self.state = MediaState()
        self.last_frame: list = []
        self.frame_id = 0
        self.input_log: list[str] = []
        self.obs_status = ""
        self.last_knob: tuple[int, float] = (-1, 0.0)  # (índice, momento) de la última perilla usada
        obs_cfg = config["obs"]
        self.obs = OBSActions(OBSClient(obs_cfg["host"], obs_cfg["port"], obs_cfg["password"]))

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._events: "queue.Queue[InputEvent]" = queue.Queue()
        self._actions: "queue.Queue" = queue.Queue()
        self._coalesce: dict[str, Callable[[], None]] = {}
        self._threads: list[threading.Thread] = []

        self._last_sent: list[Optional[bytes]] = [None] * NUM_KEYS
        self._device_failed = threading.Event()
        self._overlay: Optional[Overlay] = None
        self._overlay_until = 0.0
        self._local_volume_until = 0.0
        self._local_app_volume_until = 0.0
        self._local_mic_until = 0.0
        self._last_scene_knob = 0.0
        self._local_status_until = 0.0
        self._last_track_knob = 0.0
        self._identify_until = 0.0
        self._save_at: Optional[float] = None
        self._last_status_logged = ""

    # ================================================================ ciclo de vida
    def start(self) -> None:
        for target, name in ((self._media_loop, "media"), (self._action_loop, "actions"),
                             (self._main_loop, "main")):
            t = threading.Thread(target=target, name=f"akp03-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._refresh.set()
        self._actions.put(None)
        for t in self._threads:
            t.join(timeout=3)
        self._close_device(blank=True)
        self.obs.client.close()
        if self._save_at is not None:
            self._save()
        try:
            self.backend.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def running(self) -> bool:
        return bool(self._threads) and not self._stop.is_set()

    # ================================================================ configuración
    def apply_config(self, config: dict) -> None:
        """Aplica una configuración nueva en caliente."""
        with self._lock:
            old_dev = self.config.get("device", {})
            self.config = config
            self.renderer.update_config(config)
            self._last_sent = [None] * NUM_KEYS
            new_dev = config["device"]
            reconnect = any(old_dev.get(k) != new_dev.get(k) for k in ("vid", "pid", "packet_size"))
        obs_cfg = config["obs"]
        self.obs.client.configure(obs_cfg["host"], obs_cfg["port"], obs_cfg["password"])
        if self.device is not None:
            if reconnect:
                self._device_failed.set()
            else:
                self.device.input_map = build_input_map(new_dev.get("input_map"))
                self._safe_device(lambda d: d.set_brightness(new_dev["brightness"]))

    def identify(self, seconds: float = 8.0) -> None:
        """Muestra los números 1..6 en las teclas durante unos segundos."""
        self._identify_until = time.monotonic() + seconds

    def inject(self, event: InputEvent) -> None:
        """Simula una entrada (usado por la vista previa de la interfaz)."""
        self._events.put(event)

    def _save(self) -> None:
        self._save_at = None
        try:
            save_config(self.config, self.config_path)
        except OSError as exc:
            log.error("No se pudo guardar la configuración: %s", exc)

    # ================================================================ dispositivo
    @staticmethod
    def _default_open(cfg: dict) -> AKP03Device:
        return open_from_config(cfg)

    def _set_status(self, text: str, level=logging.INFO) -> None:
        self.status = text
        if text != self._last_status_logged:
            self._last_status_logged = text
            log.log(level, text)

    def _try_connect(self) -> None:
        try:
            dev = self._device_opener(self.config)
            dev.initialize(self.config["device"]["brightness"])
        except DeviceError as exc:
            self._set_status(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Error abriendo el dispositivo: {exc}", logging.ERROR)
            return
        self._device_failed.clear()
        self._last_sent = [None] * NUM_KEYS
        with self._lock:
            self.renderer.set_auto_key_size(dev.profile.key_size)
        self.device = dev
        self._set_status(f"Conectado: {dev.name}")
        t = threading.Thread(target=self._reader_loop, args=(dev,), name="akp03-reader", daemon=True)
        t.start()

    def _close_device(self, blank: bool = False) -> None:
        dev, self.device = self.device, None
        if dev is not None:
            dev.close(blank=blank)

    def _safe_device(self, fn: Callable[[AKP03Device], None]) -> None:
        dev = self.device
        if dev is None:
            return
        try:
            fn(dev)
        except DeviceError as exc:
            log.warning("Error del dispositivo: %s", exc)
            self._device_failed.set()

    def _reader_loop(self, dev: AKP03Device) -> None:
        while not self._stop.is_set() and self.device is dev:
            try:
                raw = dev.read_raw(100)
            except DeviceError as exc:
                if self.device is dev:
                    log.warning("Se perdió la conexión: %s", exc)
                    self._device_failed.set()
                return
            if not raw:
                continue
            ev = dev.parse(raw)
            if ev is not None:
                self._events.put(ev)

    def _send_frame(self, images) -> None:
        dev = self.device
        if dev is None:
            return
        dcfg = self.config["device"]
        ids = dcfg["image_key_ids"]
        rotation = dcfg["rotation"] if dcfg["rotation"] is not None else dev.profile.rotation
        changed = False
        for i, img in enumerate(images):
            data = encode_key(img, rotation, dcfg["flip"], dcfg["jpeg_quality"])
            if data == self._last_sent[i]:
                continue
            dev.set_key_image(int(ids[i]), data)
            self._last_sent[i] = data
            changed = True
        if changed:
            dev.flush()

    # ================================================================ bucles
    def _main_loop(self) -> None:
        next_connect = 0.0
        last_keepalive = time.monotonic()
        while not self._stop.is_set():
            frame_start = time.monotonic()

            if self._device_failed.is_set() and self.device is not None:
                self._close_device()
                self._set_status("Dispositivo desconectado; reintentando…", logging.WARNING)
                next_connect = frame_start + 1.0
            if self.use_device and self.device is None and frame_start >= next_connect:
                self._try_connect()
                next_connect = frame_start + RECONNECT_SECONDS
                last_keepalive = frame_start

            if self._save_at is not None and frame_start >= self._save_at:
                self._save()

            try:
                images = self._render(frame_start)
                self.last_frame = images
                self.frame_id += 1
            except Exception:  # noqa: BLE001
                log.exception("Error al renderizar")
                images = None

            if images is not None and self.device is not None:
                try:
                    self._send_frame(images)
                    if frame_start - last_keepalive > KEEPALIVE_SECONDS:
                        self.device.keep_alive()
                        last_keepalive = frame_start
                except DeviceError as exc:
                    log.warning("Error enviando imágenes: %s", exc)
                    self._device_failed.set()

            # Espera hasta el siguiente cuadro, pero responde al instante a las entradas.
            deadline = frame_start + 1.0 / self.config["display"]["fps"]
            while not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    ev = self._events.get(timeout=remaining)
                except queue.Empty:
                    break
                self._handle_event(ev)
                # Procesa el resto de eventos pendientes y vuelve a dibujar ya.
                while True:
                    try:
                        self._handle_event(self._events.get_nowait())
                    except queue.Empty:
                        break
                break
        self._close_device(blank=True)

    def _render(self, now: float):
        if now < self._identify_until:
            return self.renderer.render_identify()
        with self._lock:
            state = self.state
        overlay = self._overlay if now < self._overlay_until else None
        return self.renderer.render(state, now, overlay=overlay)

    def _media_loop(self) -> None:
        while not self._stop.is_set():
            try:
                st = self.backend.get_state()
            except Exception as exc:  # noqa: BLE001
                log.debug("Error consultando medios: %s", exc)
                st = None
            if st is not None:
                now = time.monotonic()
                with self._lock:
                    prev = self.state
                    if now < self._local_volume_until:
                        st = st.copy(volume=prev.volume, muted=prev.muted)
                    elif prev.volume is not None and st.volume is not None and (
                            st.volume != prev.volume or st.muted != prev.muted):
                        # El volumen cambió desde fuera (teclado, Windows): muéstralo.
                        self.show_overlay(self._master_overlay(st))
                    if now < self._local_app_volume_until and st.app == prev.app:
                        st = st.copy(app_volume=prev.app_volume, app_muted=prev.app_muted)
                    if now < self._local_mic_until:
                        st = st.copy(mic_muted=prev.mic_muted)
                    if now < self._local_status_until and st.track_key == prev.track_key:
                        st = st.copy(status=prev.status)
                    self.state = st
            self._refresh.wait(float(self.config["media"]["poll_interval"]))
            self._refresh.clear()

    def _action_loop(self) -> None:
        while True:
            item = self._actions.get()
            if item is None or self._stop.is_set():
                return
            if isinstance(item, str):
                with self._lock:
                    fn = self._coalesce.pop(item, None)
                if fn is None:
                    continue
            else:
                fn = item
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                log.warning("La acción falló: %s", exc)
            self._refresh.set()

    def _submit(self, fn: Callable[[], None], coalesce_key: Optional[str] = None) -> None:
        if coalesce_key is None:
            self._actions.put(fn)
            return
        with self._lock:
            pending = coalesce_key in self._coalesce
            self._coalesce[coalesce_key] = fn
        if not pending:
            self._actions.put(coalesce_key)

    # ================================================================ entradas
    def _handle_event(self, ev: InputEvent) -> None:
        desc = f"{ev.control}{ev.index + 1} {ev.kind}" + (f" {ev.value:+d}" if ev.kind == "turn" else "")
        self.input_log = (self.input_log + [f"{desc} (código {ev.code:#04x})"])[-20:]
        log.debug("Entrada: %s", desc)

        cfg = self.config
        if ev.control == "key" and ev.kind == "press" and 0 <= ev.index < len(cfg["keys"]):
            key = cfg["keys"][ev.index]
            self.do_action(key.get("action") or DEFAULT_KEY_ACTION.get(key["type"], "none"),
                           key.get("target"))
        elif ev.control == "button" and ev.kind == "press" and 0 <= ev.index < len(cfg["buttons"]):
            btn = cfg["buttons"][ev.index]
            self.do_action(btn.get("action", "none"), btn.get("target"))
        elif ev.control == "knob" and 0 <= ev.index < len(cfg["knobs"]):
            knob = cfg["knobs"][ev.index]
            self.last_knob = (ev.index, time.monotonic())
            if ev.kind == "turn":
                self.knob_turn(knob.get("turn", "none"), ev.value, knob.get("target"))
            elif ev.kind == "press":
                self.do_action(knob.get("press", "none"), knob.get("target"))
        elif ev.control == "unknown":
            log.info("Entrada desconocida, código %#04x. Puedes mapearla en "
                     "device.input_map de la configuración.", ev.code)

    # ================================================================ avisos en pantalla
    def show_overlay(self, overlay: Overlay, seconds: Optional[float] = None) -> None:
        self._overlay = overlay
        self._overlay_until = time.monotonic() + (
            seconds if seconds is not None else float(self.config["display"]["volume_overlay_seconds"]))

    @staticmethod
    def _master_overlay(st: MediaState) -> Overlay:
        vol = st.volume or 0
        return Overlay("mute" if st.muted else "volume", "Mute" if st.muted else f"{vol}%",
                       "Volumen PC", vol / 100, st.muted)

    @staticmethod
    def _app_overlay(st: MediaState) -> Overlay:
        vol = st.app_volume or 0
        return Overlay("mute" if st.app_muted else "volume", "Mute" if st.app_muted else f"{vol}%",
                       st.app or "Música", vol / 100, st.app_muted)

    @staticmethod
    def _mic_overlay(muted: Optional[bool]) -> Overlay:
        if muted is None:
            return Overlay("mic", "Sin mic", "Micrófono")
        return Overlay("mic_off" if muted else "mic", "Mic OFF" if muted else "Mic ON",
                       "Micrófono", None, bool(muted))

    # ================================================================ acciones
    def do_action(self, action: str, target: Optional[str] = None) -> None:
        now = time.monotonic()
        b = self.backend
        if action == "play_pause":
            with self._lock:
                if self.state.has_media:
                    self.state = self.state.copy(
                        status="paused" if self.state.playing else "playing",
                        position=self.state.position_at(now), sampled_at=now)
                    self._local_status_until = now + 1.0
            self._submit(b.play_pause)
        elif action == "next":
            self._submit(b.next)
        elif action == "previous":
            self._submit(b.previous)
        elif action == "volume_up":
            self.change_volume(int(self.config["volume_step"]) * 2)
        elif action == "volume_down":
            self.change_volume(-int(self.config["volume_step"]) * 2)
        elif action == "mute":
            with self._lock:
                self.state = self.state.copy(muted=not self.state.muted)
                self._local_volume_until = now + 1.0
                self.show_overlay(self._master_overlay(self.state))
            self._submit(b.toggle_mute)
        elif action == "app_mute":
            self._toggle_app_mute()
        elif action == "mic_mute":
            self._toggle_mic()
        elif action == "brightness_up":
            self.change_brightness(10)
        elif action == "brightness_down":
            self.change_brightness(-10)
        elif action.startswith("obs_"):
            self._submit(lambda: self._run_obs(lambda: self.obs.run(action, target)))
        elif action == "open" and target:
            self._submit(lambda: open_target(target))

    def knob_turn(self, action: str, value: int, target: Optional[str] = None) -> None:
        now = time.monotonic()
        if action == "app_volume":
            self.change_app_volume(value * int(self.config["volume_step"]))
        elif action == "volume":
            self.change_volume(value * int(self.config["volume_step"]))
        elif action == "track":
            if now - self._last_track_knob >= TRACK_KNOB_DEBOUNCE:
                self._last_track_knob = now
                self.do_action("next" if value > 0 else "previous")
        elif action == "seek":
            delta = value * float(self.config["seek_step"])
            with self._lock:
                pos = self.state.position_at(now)
                if pos is not None:
                    dur = self.state.duration or pos + delta
                    self.state = self.state.copy(position=max(0.0, min(dur, pos + delta)), sampled_at=now)
            self._submit(lambda: self.backend.seek(delta))
        elif action == "brightness":
            self.change_brightness(value * 5)
        elif action == "obs_scene_cycle":
            if now - self._last_scene_knob >= TRACK_KNOB_DEBOUNCE:
                self._last_scene_knob = now
                self._submit(lambda: self._run_obs(lambda: self.obs.cycle_scene(value)))
        elif action == "obs_volume":
            step = value * float(self.config["obs_volume_step_db"])
            self._submit(lambda: self._run_obs(lambda: self.obs.change_volume(target, step)))

    def _run_obs(self, fn: Callable) -> None:
        """Ejecuta una acción de OBS (en el hilo de acciones) y muestra el resultado."""
        try:
            fb = fn()
        except OBSError as exc:
            self.obs_status = str(exc)
            log.warning("OBS: %s", exc)
            if isinstance(exc, OBSConnectionLost) or "no está conectado" in str(exc):
                text = "Cerrado"
            elif "contraseña" in str(exc).lower():
                text = "Clave mal"
            else:
                text = "Error"
            self.show_overlay(Overlay("scene", text, "OBS", alert=True), 2.5)
            return
        self.obs_status = "Conectado"
        self.show_overlay(Overlay(fb.icon, fb.text, fb.subtitle, fb.frac, fb.alert), 2.0)

    def change_volume(self, delta: int) -> None:
        """Volumen general de Windows."""
        now = time.monotonic()
        with self._lock:
            current = self.state.volume
            if current is None:
                target = None
                self.show_overlay(Overlay("volume", "+" if delta > 0 else "−", "Volumen PC"))
            else:
                target = max(0, min(100, current + delta))
                muted = self.state.muted and not (delta > 0)
                self.state = self.state.copy(volume=target, muted=muted)
                self._local_volume_until = now + 1.0
                self.show_overlay(self._master_overlay(self.state))
        if target is None:
            self._submit(lambda: self.backend.change_volume(delta))
        else:
            self._submit(lambda: self.backend.set_volume(target), coalesce_key="volume")

    def change_app_volume(self, delta: int) -> None:
        """Volumen sólo de la app que suena (Spotify, el navegador con YouTube…).

        Si no se encuentra la app en el mezclador (p. ej. no suena nada), se
        cambia el volumen general.
        """
        now = time.monotonic()
        with self._lock:
            current = self.state.app_volume
            if current is not None:
                target = max(0, min(100, current + delta))
                muted = self.state.app_muted and not (delta > 0)
                self.state = self.state.copy(app_volume=target, app_muted=muted)
                self._local_app_volume_until = now + 1.0
                self.show_overlay(self._app_overlay(self.state))
        if current is None:
            self.change_volume(delta)
            return

        def apply():
            if not self.backend.set_app_volume(target):
                log.debug("La app %s no está en el mezclador", self.state.app)

        self._submit(apply, coalesce_key="app_volume")

    def _toggle_app_mute(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self.state.app_volume is None:
                found = False
            else:
                found = True
                self.state = self.state.copy(app_muted=not self.state.app_muted)
                self._local_app_volume_until = now + 1.0
                self.show_overlay(self._app_overlay(self.state))
        if found:
            self._submit(self.backend.toggle_app_mute)
        else:
            self.do_action("mute")

    def _toggle_mic(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self.state.mic_muted is not None:
                self.state = self.state.copy(mic_muted=not self.state.mic_muted)
                self._local_mic_until = now + 1.5
                self.show_overlay(self._mic_overlay(self.state.mic_muted))

        def apply():
            new = self.backend.toggle_mic_mute()
            with self._lock:
                self.state = self.state.copy(mic_muted=new)
                self._local_mic_until = time.monotonic() + 1.5
            self.show_overlay(self._mic_overlay(new))

        self._submit(apply)

    def change_brightness(self, delta: int) -> None:
        dev_cfg = self.config["device"]
        dev_cfg["brightness"] = max(0, min(100, int(dev_cfg["brightness"]) + delta))
        value = dev_cfg["brightness"]
        self._safe_device(lambda d: d.set_brightness(value))
        self._save_at = time.monotonic() + 2.0
