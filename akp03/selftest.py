"""Autoprueba del ejecutable: ``AKP03Controller.exe --self-test resultado.txt``.

Comprueba que el paquete compilado incluye todo lo necesario (librerías, tema,
WinRT, audio, Tk). La usa la integración continua tras compilar en Windows;
también sirve para diagnosticar una instalación.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from typing import Callable

REQUIRED_MODULES = ["PIL.Image", "PIL.ImageTk", "tkinter", "sv_ttk", "websocket", "hid",
                    "akp03.gui", "akp03.power", "akp03.instance"]
WINDOWS_MODULES = ["pystray", "pystray._win32", "pycaw.pycaw", "comtypes", "psutil",
                   "winrt.windows.media.control", "winrt.windows.storage.streams"]


def run(out_path: str) -> int:
    lines: list[str] = []
    failed = False

    def check(name: str, fn: Callable[[], object], required: bool = True) -> None:
        nonlocal failed
        try:
            detail = fn()
            lines.append(f"OK   {name}" + (f": {detail}" if detail else ""))
        except Exception as exc:  # noqa: BLE001
            failed = failed or required
            tag = "FAIL" if required else "WARN"
            lines.append(f"{tag} {name}: {exc.__class__.__name__}: {exc}")
            if required:
                lines.append("     " + traceback.format_exc().replace("\n", "\n     "))

    for mod in REQUIRED_MODULES + (WINDOWS_MODULES if sys.platform == "win32" else []):
        check(f"import {mod}", lambda m=mod: importlib.import_module(m) and None)

    def render():
        from .config import normalize
        from .media.demo import DemoBackend
        from .render import Renderer, encode_key
        r = Renderer(normalize({}))
        imgs = r.render(DemoBackend().get_state())
        data = encode_key(imgs[0], 90)
        return f"6 teclas, JPEG de {len(data)} bytes"

    def fonts():
        from .render import font, font_for
        return f"{getattr(font(14, True), 'path', '?')} | CJK: {getattr(font_for('日本', 14), 'path', '?')}"

    def tk_theme():
        import tkinter as tk
        import sv_ttk
        root = tk.Tk()
        try:
            root.withdraw()
            sv_ttk.set_theme("dark", root)
            return f"Tk {root.tk.call('info', 'patchlevel')}, tema {sv_ttk.get_theme(root)}"
        finally:
            root.destroy()

    def hid_enum():
        from .device import enumerate_hid
        return f"{len(enumerate_hid())} dispositivos HID"

    check("dibujar teclas", render)
    check("fuentes", fonts)
    check("ventana Tk + tema", tk_theme)
    check("listar USB-HID", hid_enum)

    if sys.platform == "win32":
        def media():
            from .media.windows import WindowsMediaBackend
            b = WindowsMediaBackend()
            try:
                st = b.get_state()
                return f"estado={st.status} volumen={st.volume}"
            finally:
                b.close()

        # En un servidor sin audio/sesión multimedia esto puede fallar: aviso, no error.
        check("API multimedia y volumen de Windows", media, required=False)

    lines.append("RESULTADO: " + ("FALLÓ" if failed else "OK"))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 1 if failed else 0
