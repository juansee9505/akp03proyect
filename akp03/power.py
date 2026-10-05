"""Avisos de energía del sistema: apagar, suspender, apagado del monitor.

Al apagar el PC, Windows termina el programa sin darle tiempo a limpiar, y
muchas placas base siguen alimentando los USB: el AKP03 se quedaría encendido
con la última imagen. Para evitarlo se crea una ventana invisible que recibe
los mensajes de Windows (WM_QUERYENDSESSION, WM_POWERBROADCAST…) y apaga la
pantalla del dispositivo en el momento justo.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013
# GUID_CONSOLE_DISPLAY_STATE: 0 = monitor apagado, 1 = encendido, 2 = atenuado
DISPLAY_OFF, DISPLAY_ON, DISPLAY_DIMMED = 0, 1, 2


class PowerEvents:
    """Traduce los mensajes de Windows a llamadas al controlador.

    Separado de la parte de Win32 para poder probarlo en cualquier sistema.
    """

    def __init__(self, on_shutdown: Callable[[], None], on_suspend: Callable[[], None],
                 on_resume: Callable[[], None], on_display: Callable[[bool], None],
                 display_enabled: Callable[[], bool] = lambda: True):
        self.on_shutdown = on_shutdown
        self.on_suspend = on_suspend
        self.on_resume = on_resume
        self.on_display = on_display
        self.display_enabled = display_enabled
        self._suspended = False

    def dispatch(self, msg: int, wparam: int, setting_value: Optional[int] = None) -> Optional[int]:
        """Procesa un mensaje. Devuelve el valor a responder a Windows, o None
        si debe ir al procedimiento por defecto."""
        try:
            if msg == WM_QUERYENDSESSION:
                # Se apaga ya: tras este mensaje Windows puede terminar el proceso
                # sin avisar más.
                self.on_shutdown()
                return 1  # TRUE: no bloqueamos el apagado
            if msg == WM_ENDSESSION:
                if wparam:
                    self.on_shutdown()
                else:  # otro programa canceló el apagado
                    self.on_resume()
                return 0
            if msg == WM_POWERBROADCAST:
                if wparam == PBT_APMSUSPEND:
                    self._suspended = True
                    self.on_suspend()
                elif wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                    if self._suspended:  # ambos llegan al despertar: sólo uno cuenta
                        self._suspended = False
                        self.on_resume()
                elif wparam == PBT_POWERSETTINGCHANGE and setting_value is not None:
                    if self.display_enabled():
                        if setting_value == DISPLAY_OFF:
                            self.on_display(False)
                        elif setting_value == DISPLAY_ON:
                            self.on_display(True)
                return 1
        except Exception:  # noqa: BLE001  (nunca romper el bucle de mensajes)
            log.exception("Error atendiendo el aviso de energía %#x", msg)
            return 1 if msg in (WM_QUERYENDSESSION, WM_POWERBROADCAST) else 0
        return None


class PowerMonitor:
    """Ventana oculta de Windows que recibe los avisos de energía (no hace nada
    en otros sistemas)."""

    def __init__(self, events: PowerEvents):
        self.events = events
        self._thread: Optional[threading.Thread] = None
        self._hwnd = None
        self._ready = threading.Event()

    def start(self) -> None:
        if sys.platform != "win32":
            return
        self._thread = threading.Thread(target=self._run, name="akp03-power", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        if self._hwnd:
            try:
                import ctypes
                ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:  # pragma: no cover - sólo Windows
        try:
            self._loop()
        except Exception:  # noqa: BLE001
            log.exception("No se pudieron registrar los avisos de energía de Windows")
        finally:
            self._ready.set()

    def _loop(self) -> None:  # pragma: no cover - sólo Windows
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        class POWERBROADCAST_SETTING(ctypes.Structure):
            _fields_ = [("PowerSetting", GUID), ("DataLength", wintypes.DWORD),
                        ("Data", ctypes.c_ubyte * 1)]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                           wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                           wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID),
                                                            wintypes.DWORD]
        user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
        user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        data_offset = POWERBROADCAST_SETTING.Data.offset

        def wndproc(hwnd, msg, wparam, lparam):
            setting = None
            if msg == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE and lparam:
                setting = ctypes.c_ulong.from_address(lparam + data_offset).value
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            result = self.events.dispatch(msg, wparam, setting)
            if result is None:
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            return result

        proc = WNDPROC(wndproc)  # se guarda la referencia mientras viva la ventana
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.lpszClassName = "AKP03ControllerPower"
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Ventana normal de nivel superior que nunca se muestra: las ventanas
        # «sólo mensajes» no reciben los avisos de apagado.
        hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "AKP03 Controller", 0,
                                      0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd

        display_guid = GUID(0x6FE69556, 0x704A, 0x47A0,
                            (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))
        notify = user32.RegisterPowerSettingNotification(hwnd, ctypes.byref(display_guid), 0)
        self._ready.set()
        log.info("Avisos de energía de Windows activos")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if notify:
            user32.UnregisterPowerSettingNotification(notify)
        self._hwnd = None
