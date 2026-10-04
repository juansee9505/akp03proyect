"""Comunicación USB-HID con el Ajazz AKP03.

El AKP03 (y sus clones Mirabox N3 / AKP03E / AKP03R) usa el protocolo "CRT"
de Mirabox: cada comando es un paquete de `packet_size` bytes que empieza por
``CRT\\0\\0`` seguido de un comando de 3 letras:

* ``DIS``           despertar / inicializar la pantalla
* ``LIG\\0\\0<n>``   brillo 0-100
* ``CLE\\0\\0\\0<k>`` borrar la tecla k (0xFF = todas)
* ``BAT<len><k>``   a continuación se envía un JPEG de <len> bytes para la tecla k
* ``STP``           aplicar (refrescar) las imágenes enviadas
* ``CONNECT``       keep-alive
* ``HAN``           apagar la pantalla (modo reposo)

Las entradas llegan como reportes que empiezan por ``ACK\\0\\0OK\\0\\0`` con el
código del control en el byte 9 y el estado (1 = pulsado, 0 = soltado) en el 10.

No es un protocolo documentado oficialmente: los valores salen de proyectos de
código abierto (mirajazz, opendeck-akp03, elgato-streamdeck). Si tu unidad usa
otros códigos, ejecuta ``akp03 --debug-input`` y ajústalos en la configuración.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

CMD_PREFIX = b"CRT\x00\x00"

# (vid, pid, nombre) conocidos.
KNOWN_DEVICES: list[tuple[int, int, str]] = [
    (0x0300, 0x1001, "Ajazz AKP03"),
    (0x0300, 0x1003, "Ajazz AKP03R"),
    (0x0300, 0x3002, "Ajazz AKP03E"),
    (0x6603, 0x1002, "Mirabox N3"),
    (0x6603, 0x1003, "Mirabox N3EN"),
]
AJAZZ_VENDOR_IDS = {0x0300}
NAME_HINTS = ("akp03", "ajazz", "mirabox", "stream dock", "streamdock")

# Código del reporte de entrada -> nombre del control.
DEFAULT_INPUT_MAP: dict[int, str] = {
    0x01: "key1", 0x02: "key2", 0x03: "key3",
    0x04: "key4", 0x05: "key5", 0x06: "key6",
    0x25: "button1", 0x30: "button2", 0x31: "button3",
    0x90: "knob1-", 0x91: "knob1+",
    0x50: "knob2-", 0x51: "knob2+",
    0x60: "knob3-", 0x61: "knob3+",
    0x33: "knob1press", 0x35: "knob2press", 0x34: "knob3press",
}

_CONTROL_RE = re.compile(r"^(key|button|knob)(\d+)(\+|-|press)?$")


class DeviceError(Exception):
    pass


@dataclass(frozen=True)
class InputEvent:
    """Evento de entrada.

    control: "key", "button", "knob" o "unknown"
    index:   0-based
    kind:    "press", "release" o "turn"
    value:   +1/-1 para "turn"
    """

    control: str
    index: int
    kind: str
    value: int = 0
    code: int = 0


def build_input_map(overrides: dict | None = None) -> dict[int, str]:
    mapping = dict(DEFAULT_INPUT_MAP)
    for code, name in (overrides or {}).items():
        try:
            code_int = int(code, 0) if isinstance(code, str) else int(code)
        except ValueError:
            log.warning("Código de entrada inválido en la configuración: %r", code)
            continue
        if name and _CONTROL_RE.match(name):
            mapping[code_int] = name
        elif not name:
            mapping.pop(code_int, None)
    return mapping


def parse_report(data: bytes, input_map: dict[int, str] | None = None) -> Optional[InputEvent]:
    """Convierte un reporte HID crudo en un InputEvent (o None si no aplica)."""
    if not data:
        return None
    data = bytes(data)
    base = data.find(b"ACK")
    if base < 0:
        base = 0
    if len(data) < base + 11:
        return None
    code, state = data[base + 9], data[base + 10]
    if code == 0:
        return None
    name = (input_map or DEFAULT_INPUT_MAP).get(code)
    m = _CONTROL_RE.match(name) if name else None
    if not m:
        return InputEvent("unknown", 0, "press" if state else "release", 0, code)
    control, num, suffix = m.group(1), int(m.group(2)) - 1, m.group(3)
    if control == "knob" and suffix in ("+", "-"):
        return InputEvent("knob", num, "turn", 1 if suffix == "+" else -1, code)
    return InputEvent(control, num, "press" if state else "release", 0, code)


# --------------------------------------------------------------------------
# Acceso HID (soporta los paquetes "hidapi" y "hid" de PyPI)
# --------------------------------------------------------------------------

def _hid_module():
    try:
        import hid  # type: ignore
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise DeviceError(
            "Falta la librería hidapi. Instálala con: pip install hidapi"
        ) from exc
    return hid


def enumerate_hid() -> list[dict]:
    hid = _hid_module()
    try:
        return list(hid.enumerate())
    except Exception as exc:  # pragma: no cover
        raise DeviceError(f"No se pudieron listar los dispositivos HID: {exc}") from exc


def _matches(info: dict, vid: int | None, pid: int | None) -> int:
    """Puntuación de qué tan probable es que `info` sea un AKP03 (0 = no)."""
    v, p = info.get("vendor_id"), info.get("product_id")
    if vid is not None:
        return 100 if v == vid and (pid is None or p == pid) else 0
    if any(v == kv and p == kp for kv, kp, _ in KNOWN_DEVICES):
        return 90
    if v in AJAZZ_VENDOR_IDS:
        return 60
    name = f"{info.get('manufacturer_string') or ''} {info.get('product_string') or ''}".lower()
    if any(h in name for h in NAME_HINTS):
        return 50
    return 0


def find_candidates(infos: Iterable[dict], vid: int | None = None, pid: int | None = None) -> list[dict]:
    scored = []
    for info in infos:
        score = _matches(info, vid, pid)
        if not score:
            continue
        # La interfaz 0 / usage page de vendor suele ser la de datos.
        if info.get("interface_number") in (0, -1):
            score += 5
        if (info.get("usage_page") or 0) >= 0xFF00:
            score += 3
        scored.append((score, info))
    scored.sort(key=lambda t: -t[0])
    return [info for _, info in scored]


class _HidHandle:
    """Adaptador mínimo sobre los dos paquetes de Python llamados `hid`."""

    def __init__(self, path: bytes):
        hid = _hid_module()
        if hasattr(hid, "device"):  # paquete "hidapi" (cython-hidapi)
            self._dev = hid.device()
            self._dev.open_path(path)
            self._kind = "hidapi"
        else:  # paquete "hid" (ctypes)
            self._dev = hid.Device(path=path)
            self._kind = "hid"

    def write(self, data: bytes) -> int:
        return self._dev.write(data)

    def read(self, size: int, timeout_ms: int) -> bytes:
        if self._kind == "hidapi":
            return bytes(self._dev.read(size, timeout_ms))
        return bytes(self._dev.read(size, timeout_ms) or b"")

    def close(self) -> None:
        self._dev.close()


class AKP03Device:
    """Conexión abierta con el AKP03."""

    def __init__(self, handle: Any, packet_size: int = 512, info: dict | None = None,
                 input_map: dict[int, str] | None = None):
        self._hid = handle
        self.packet_size = int(packet_size)
        self.info = info or {}
        self.input_map = input_map or dict(DEFAULT_INPUT_MAP)
        self._lock = threading.Lock()
        self._closed = False

    # ----------------------------------------------------------- apertura
    @classmethod
    def open(cls, vid: int | None = None, pid: int | None = None, packet_size: int = 512,
             input_map: dict[int, str] | None = None) -> "AKP03Device":
        candidates = find_candidates(enumerate_hid(), vid, pid)
        if not candidates:
            raise DeviceError("No se encontró ningún AKP03 conectado")
        errors = []
        for info in candidates:
            try:
                handle = _HidHandle(info["path"])
            except Exception as exc:
                errors.append(f"{info.get('product_string')}: {exc}")
                continue
            dev = cls(handle, packet_size, info, input_map)
            log.info("Conectado a %s (VID %04x PID %04x)", dev.name,
                     info.get("vendor_id", 0), info.get("product_id", 0))
            return dev
        raise DeviceError(
            "Se encontró el AKP03 pero no se pudo abrir (¿está abierto el software "
            "oficial de Ajazz?): " + "; ".join(errors)
        )

    @property
    def name(self) -> str:
        return self.info.get("product_string") or "AKP03"

    # ----------------------------------------------------------- escritura
    def _write_packet(self, payload: bytes) -> None:
        if self._closed:
            raise DeviceError("Dispositivo cerrado")
        if len(payload) > self.packet_size:
            raise ValueError("payload demasiado grande para un paquete")
        # El primer byte es el report ID (0 = sin report ID).
        report = b"\x00" + payload + b"\x00" * (self.packet_size - len(payload))
        try:
            written = self._hid.write(report)
        except Exception as exc:
            raise DeviceError(f"Error escribiendo al dispositivo: {exc}") from exc
        if written is not None and written < 0:
            raise DeviceError("Error escribiendo al dispositivo")

    def _command(self, body: bytes) -> None:
        with self._lock:
            self._write_packet(CMD_PREFIX + body)

    def wake(self) -> None:
        self._command(b"DIS")

    def set_brightness(self, percent: int) -> None:
        self._command(b"LIG\x00\x00" + bytes([max(0, min(100, int(percent)))]))

    def clear(self, key_id: int = 0xFF) -> None:
        self._command(b"CLE\x00\x00\x00" + bytes([key_id & 0xFF]))

    def flush(self) -> None:
        self._command(b"STP")

    def keep_alive(self) -> None:
        self._command(b"CONNECT")

    def sleep(self) -> None:
        self._command(b"HAN")

    def set_key_image(self, key_id: int, jpeg: bytes) -> None:
        """Envía un JPEG a una tecla. Llama a flush() después para mostrarlo."""
        with self._lock:
            self._write_packet(CMD_PREFIX + b"BAT" + len(jpeg).to_bytes(4, "big") + bytes([key_id & 0xFF]))
            for i in range(0, len(jpeg), self.packet_size):
                self._write_packet(jpeg[i:i + self.packet_size])

    def initialize(self, brightness: int) -> None:
        self.wake()
        self.set_brightness(brightness)
        self.clear()
        self.flush()

    # ----------------------------------------------------------- lectura
    def read_raw(self, timeout_ms: int = 100) -> bytes:
        if self._closed:
            raise DeviceError("Dispositivo cerrado")
        try:
            return self._hid.read(self.packet_size + 1, timeout_ms)
        except Exception as exc:
            raise DeviceError(f"Error leyendo del dispositivo: {exc}") from exc

    def read_event(self, timeout_ms: int = 100) -> Optional[InputEvent]:
        return parse_report(self.read_raw(timeout_ms), self.input_map)

    # ----------------------------------------------------------- cierre
    def close(self, blank: bool = True) -> None:
        if self._closed:
            return
        if blank:
            try:
                self.clear()
                self.flush()
            except DeviceError:
                pass
        self._closed = True
        try:
            self._hid.close()
        except Exception:
            pass


def describe_hid_devices() -> str:
    """Texto con todos los dispositivos HID (para diagnóstico)."""
    lines = []
    for info in enumerate_hid():
        mark = " <- probable AKP03" if _matches(info, None, None) else ""
        lines.append(
            "VID {:04x} PID {:04x} if={} usage_page={:#06x} '{}' '{}'{}".format(
                info.get("vendor_id", 0), info.get("product_id", 0),
                info.get("interface_number"), info.get("usage_page") or 0,
                info.get("manufacturer_string") or "", info.get("product_string") or "", mark,
            )
        )
    return "\n".join(lines) or "(no hay dispositivos HID)"
