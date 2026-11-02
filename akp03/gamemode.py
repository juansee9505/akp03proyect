"""Convivir con juegos: prioridad baja y detección de juegos a pantalla completa.

El programa trabaja de forma continua (consulta la música, desplaza el texto,
envía imágenes por USB). Para no quitarle ni un milisegundo a un juego:

* el proceso corre con prioridad «por debajo de lo normal» y, en Windows 11,
  con EcoQoS (Windows lo manda a los núcleos de eficiencia);
* si hay una aplicación a pantalla completa delante (también «ventana sin
  bordes»), el controlador baja su ritmo de trabajo.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
ProcessPowerThrottling = 4
# SHQueryUserNotificationState
QUNS_BUSY = 2  # aplicación a pantalla completa
QUNS_RUNNING_D3D_FULL_SCREEN = 3  # juego Direct3D en pantalla completa exclusiva
QUNS_PRESENTATION_MODE = 4
# Ventanas del escritorio que cubren la pantalla pero no son un juego.
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def lower_process_priority() -> None:
    """Prioridad baja + EcoQoS para no competir con juegos. Sólo Windows."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel32.GetCurrentProcess()
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    if not kernel32.SetPriorityClass(process, BELOW_NORMAL_PRIORITY_CLASS):
        log.debug("No se pudo bajar la prioridad: %s", ctypes.get_last_error())

    class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]

    try:  # Windows 10 1709+ / 11; en versiones anteriores no existe y no pasa nada
        state = PROCESS_POWER_THROTTLING_STATE(PROCESS_POWER_THROTTLING_CURRENT_VERSION,
                                               PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
                                               PROCESS_POWER_THROTTLING_EXECUTION_SPEED)
        kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetProcessInformation(process, ProcessPowerThrottling,
                                       ctypes.byref(state), ctypes.sizeof(state))
    except (AttributeError, OSError) as exc:
        log.debug("EcoQoS no disponible: %s", exc)


def fullscreen_app_active() -> bool:
    """True si delante hay una aplicación a pantalla completa (juego, vídeo,
    presentación), incluida la «ventana sin bordes». Siempre False fuera de Windows."""
    if sys.platform != "win32":
        return False
    try:
        return _fullscreen_windows()
    except Exception as exc:  # noqa: BLE001  (nunca romper el programa por esto)
        log.debug("No se pudo comprobar la pantalla completa: %s", exc)
        return False


def _fullscreen_windows() -> bool:  # pragma: no cover - sólo Windows
    import ctypes
    from ctypes import wintypes

    state = ctypes.c_int(0)
    if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:  # type: ignore[attr-defined]
        if state.value in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE):
            return True

    # Respaldo: la ventana de delante ocupa todo su monitor.
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in _SHELL_CLASSES:
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32.MonitorFromWindow.restype = wintypes.HANDLE
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return (rect.left <= m.left and rect.top <= m.top
            and rect.right >= m.right and rect.bottom >= m.bottom)
