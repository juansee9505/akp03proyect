"""Punto de entrada: `python -m akp03` o el ejecutable AKP03Controller.exe."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import threading
from pathlib import Path

from . import APP_NAME, __version__
from .config import config_dir, default_config_path, load_config


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # en el .exe sin consola stderr es None
        handlers.append(logging.StreamHandler())
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            config_dir() / "akp03.log", maxBytes=512 * 1024, backupCount=1, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=level, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _debug_input(cfg: dict) -> int:
    """Muestra los reportes crudos del dispositivo para mapear controles."""
    from .controller import open_from_config

    dev = open_from_config(cfg)
    dev.initialize(cfg["device"]["brightness"])
    print(f"Conectado a {dev.name}. Pulsa teclas / gira perillas (Ctrl+C para salir).")
    try:
        while True:
            raw = dev.read_raw(500)
            if not raw:
                continue
            ev = dev.parse(raw)
            print(raw[:16].hex(" "), "->", ev)
    except KeyboardInterrupt:
        pass
    finally:
        dev.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="akp03", description=f"{APP_NAME} {__version__}")
    p.add_argument("--config", type=Path, help=f"archivo de configuración (por defecto {default_config_path()})")
    p.add_argument("--headless", action="store_true", help="sin ventana, sólo controla el dispositivo")
    p.add_argument("--minimized", action="store_true", help="iniciar minimizado")
    p.add_argument("--demo", action="store_true", help="usar música simulada")
    p.add_argument("--no-device", action="store_true", help="no conectar con el AKP03 (sólo vista previa)")
    p.add_argument("--list-devices", action="store_true", help="listar dispositivos HID y salir")
    p.add_argument("--debug-input", action="store_true", help="mostrar los códigos de cada tecla/perilla")
    p.add_argument("--identify", action="store_true", help="mostrar 1-6 en las teclas al iniciar")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("akp03")

    if args.list_devices:
        from .device import DeviceError, describe_hid_devices

        try:
            print(describe_hid_devices())
        except DeviceError as exc:
            print(exc)
            return 1
        return 0

    config_path = args.config or default_config_path()
    cfg = load_config(config_path)

    if args.debug_input:
        return _debug_input(cfg)

    from .controller import Controller
    from .media import create_backend

    backend = create_backend("demo" if args.demo else cfg["media"]["backend"],
                             prefer_playing=cfg["media"]["prefer_playing"])
    log.info("%s %s - backend de medios: %s", APP_NAME, __version__, backend.name)
    ctrl = Controller(cfg, backend, config_path=config_path, use_device=not args.no_device)
    ctrl.start()

    from .power import PowerEvents, PowerMonitor

    power = PowerMonitor(PowerEvents(
        on_shutdown=ctrl.power_shutdown, on_suspend=ctrl.power_suspend,
        on_resume=ctrl.power_resume, on_display=ctrl.display_changed,
        display_enabled=lambda: bool(ctrl.config["app"]["screen_off_with_monitor"])))
    power.start()
    if args.identify:
        ctrl.identify()

    if args.headless:
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
        last = ""
        while not stop.is_set():
            st = ctrl.state
            line = f"[{ctrl.status}] {st.status}: {st.title} - {st.artist} ({st.app}) vol={st.volume}"
            if line != last:
                if sys.stdout is not None:
                    print(line, flush=True)
                last = line
            stop.wait(1.0)
        power.stop()
        ctrl.stop()
        return 0

    try:
        from .gui import App
    except ImportError as exc:
        log.error("No se pudo cargar la interfaz gráfica (%s). Usa --headless.", exc)
        power.stop()
        ctrl.stop()
        return 1
    app = App(ctrl, start_minimized=args.minimized or cfg["app"]["start_minimized"])
    try:
        app.run()
    finally:
        power.stop()
        ctrl.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
