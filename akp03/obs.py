"""Control de OBS Studio mediante obs-websocket v5 (incluido en OBS 28 o superior).

En OBS: Herramientas → Ajustes del servidor WebSocket → «Habilitar servidor
WebSocket». El puerto por defecto es 4455; copia la contraseña de «Mostrar
información de conexión» en la pestaña OBS de este programa.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

RPC_VERSION = 1
MIN_DB = -60.0  # por debajo de esto se considera silencio total
MAX_DB = 0.0
SILENCE_DB = -100.0
RETRY_SECONDS = 3.0


class OBSError(Exception):
    pass


class OBSConnectionLost(OBSError):
    """La conexión se cerró o no se pudo abrir."""


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def _default_connect(url: str, timeout: float):
    try:
        import websocket  # type: ignore  (paquete websocket-client)
    except ImportError as exc:  # pragma: no cover
        raise OBSError("Falta la librería websocket-client (pip install websocket-client)") from exc
    try:
        return websocket.create_connection(url, timeout=timeout)
    except (OSError, websocket.WebSocketException) as exc:
        raise OBSConnectionLost("No se pudo conectar con OBS. ¿Está abierto y con el "
                                "servidor WebSocket habilitado?") from exc


class OBSClient:
    """Cliente síncrono mínimo de obs-websocket v5 (seguro entre hilos)."""

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = "",
                 timeout: float = 3.0, connect: Optional[Callable[[str, float], Any]] = None):
        self.host, self.port, self.password = host, int(port), password or ""
        self.timeout = timeout
        self._connect_fn = connect or _default_connect
        self._ws = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._last_fail = 0.0

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def configure(self, host: str, port: int, password: str) -> None:
        if (host, int(port), password or "") != (self.host, self.port, self.password):
            with self._lock:
                self._close()
                self.host, self.port, self.password = host, int(port), password or ""
                self._last_fail = 0.0

    def _recv(self) -> dict:
        try:
            raw = self._ws.recv()
        except Exception as exc:  # noqa: BLE001  (cerrado / tiempo agotado)
            raise OBSConnectionLost(f"Se perdió la conexión con OBS ({exc.__class__.__name__})") from exc
        if not raw:
            raise OBSConnectionLost("OBS cerró la conexión")
        return json.loads(raw)

    def _send(self, msg: dict) -> None:
        try:
            self._ws.send(json.dumps(msg))
        except Exception as exc:  # noqa: BLE001
            raise OBSConnectionLost(f"Se perdió la conexión con OBS ({exc.__class__.__name__})") from exc

    def _open(self) -> None:
        self._ws = self._connect_fn(f"ws://{self.host}:{self.port}", self.timeout)
        try:
            hello = self._recv()
            if hello.get("op") != 0:
                raise OBSError("Respuesta inesperada de OBS")
            identify: dict = {"rpcVersion": RPC_VERSION, "eventSubscriptions": 0}
            auth = hello.get("d", {}).get("authentication")
            if auth:
                if not self.password:
                    raise OBSError("OBS pide contraseña: cópiala en la pestaña OBS")
                identify["authentication"] = auth_string(self.password, auth["salt"], auth["challenge"])
            self._send({"op": 1, "d": identify})
            try:
                reply = self._recv()
            except OBSConnectionLost as exc:
                raise OBSError("OBS rechazó la conexión: contraseña incorrecta") from exc
            if reply.get("op") != 2:
                raise OBSError("OBS rechazó la conexión")
        except Exception:
            self._close()
            raise

    def _close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        with self._lock:
            self._close()

    def _request_once(self, request_type: str, data: Optional[dict]) -> dict:
        if self._ws is None:
            self._open()
        req_id = str(next(self._ids))
        msg: dict = {"op": 6, "d": {"requestType": request_type, "requestId": req_id}}
        if data:
            msg["d"]["requestData"] = data
        self._send(msg)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            reply = self._recv()
            d = reply.get("d", {})
            if reply.get("op") == 7 and d.get("requestId") == req_id:
                status = d.get("requestStatus", {})
                if not status.get("result"):
                    raise OBSError(status.get("comment") or f"OBS no pudo hacer {request_type} "
                                   f"(código {status.get('code')})")
                return d.get("responseData") or {}
        raise OBSError("OBS no respondió a tiempo")

    def request(self, request_type: str, data: Optional[dict] = None) -> dict:
        with self._lock:
            if self._ws is None and time.monotonic() - self._last_fail < RETRY_SECONDS:
                raise OBSError("OBS no está conectado")
            had_connection = self._ws is not None
            try:
                return self._request_once(request_type, data)
            except OBSConnectionLost:
                self._close()
                if not had_connection:
                    self._last_fail = time.monotonic()
                    raise
            except OBSError:
                if self._ws is None:  # p. ej. contraseña incorrecta
                    self._last_fail = time.monotonic()
                raise
            # La conexión anterior se cayó (p. ej. se reinició OBS): un reintento.
            try:
                return self._request_once(request_type, data)
            except OBSError:
                if self._ws is None:
                    self._last_fail = time.monotonic()
                raise


@dataclass
class Feedback:
    """Lo que se muestra en las teclas tras una acción de OBS."""

    icon: str
    text: str
    alert: bool = False
    frac: Optional[float] = None
    subtitle: str = "OBS"


def _db_to_frac(db: float) -> float:
    return max(0.0, min(1.0, (db - MIN_DB) / (MAX_DB - MIN_DB)))


class OBSActions:
    """Acciones de alto nivel que se pueden asignar a teclas, botones y perillas."""

    def __init__(self, client: OBSClient):
        self.client = client

    def run(self, action: str, target: Optional[str] = None) -> Feedback:
        c = self.client
        if action == "obs_record":
            active = c.request("ToggleRecord").get("outputActive")
            return Feedback("record", "Grabando" if active else "Detenida", bool(active),
                            subtitle="Grabación")
        if action == "obs_record_pause":
            c.request("ToggleRecordPause")
            paused = c.request("GetRecordStatus").get("outputPaused")
            return Feedback("pause" if paused else "record", "En pausa" if paused else "Grabando")
        if action == "obs_stream":
            active = c.request("ToggleStream").get("outputActive")
            return Feedback("stream", "En vivo" if active else "Detenido", bool(active),
                            subtitle="Transmisión")
        if action == "obs_virtualcam":
            active = c.request("ToggleVirtualCam").get("outputActive")
            return Feedback("camera", "Cámara ON" if active else "Cámara OFF")
        if action == "obs_replay":
            c.request("SaveReplayBuffer")
            return Feedback("replay", "Guardada", subtitle="Repetición")
        if action == "obs_scene":
            if not target:
                raise OBSError("Escribe el nombre de la escena")
            c.request("SetCurrentProgramScene", {"sceneName": target})
            return Feedback("scene", target, subtitle="Escena")
        if action == "obs_mute":
            if not target:
                raise OBSError("Escribe el nombre de la fuente de audio")
            muted = c.request("ToggleInputMute", {"inputName": target}).get("inputMuted")
            return Feedback("mic_off" if muted else "mic", "Silenciado" if muted else "Activo",
                            bool(muted), subtitle=target)
        raise OBSError(f"Acción de OBS desconocida: {action}")

    def cycle_scene(self, step: int) -> Feedback:
        data = self.client.request("GetSceneList")
        # OBS lista las escenas de abajo hacia arriba: se ordenan como en su interfaz.
        scenes = [s["sceneName"] for s in sorted(data.get("scenes", []),
                                                  key=lambda s: -s.get("sceneIndex", 0))]
        if not scenes:
            raise OBSError("OBS no tiene escenas")
        current = data.get("currentProgramSceneName")
        idx = scenes.index(current) if current in scenes else -1
        new = scenes[(idx + (1 if step > 0 else -1)) % len(scenes)]
        self.client.request("SetCurrentProgramScene", {"sceneName": new})
        return Feedback("scene", new, subtitle="Escena")

    def change_volume(self, target: Optional[str], step_db: float) -> Feedback:
        if not target:
            raise OBSError("Escribe el nombre de la fuente de audio")
        db = float(self.client.request("GetInputVolume", {"inputName": target}).get("inputVolumeDb", MIN_DB))
        db = max(MIN_DB, db)
        new = max(MIN_DB, min(MAX_DB, db + step_db))
        self.client.request("SetInputVolume",
                            {"inputName": target, "inputVolumeDb": SILENCE_DB if new <= MIN_DB else new})
        text = "-∞ dB" if new <= MIN_DB else f"{new:.0f} dB"
        return Feedback("volume", text, frac=_db_to_frac(new), subtitle=target)

    def lists(self) -> tuple[list[str], list[str]]:
        """(escenas, fuentes) para mostrar en la configuración."""
        scenes = [s["sceneName"] for s in sorted(self.client.request("GetSceneList").get("scenes", []),
                                                  key=lambda s: -s.get("sceneIndex", 0))]
        inputs = [i["inputName"] for i in self.client.request("GetInputList").get("inputs", [])]
        return scenes, inputs
