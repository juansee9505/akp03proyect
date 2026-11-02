"""Una sola copia del programa a la vez.

Si se abre dos veces (inicio automático + doble clic), las dos copias se
pelearían por el AKP03. La primera escucha en un puerto local; la segunda le
pide que muestre su ventana y se cierra.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

DEFAULT_PORT = 48653
HELLO = b"AKP03?show\n"
ANSWER = b"AKP03!\n"


class SingleInstance:
    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.on_show: Optional[Callable[[], None]] = None
        self._server: Optional[socket.socket] = None

    def acquire(self) -> bool:
        """True si esta es la única copia; False si ya había otra (y se le pidió
        que se muestre)."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: nadie más puede usar el puerto
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            srv.bind(("127.0.0.1", self.port))
            srv.listen(4)
        except OSError:
            srv.close()
            if self._notify_other():
                return False
            # El puerto lo usa otro programa que no es este: seguimos igualmente.
            log.warning("El puerto %d está ocupado por otro programa; no se comprueba si "
                        "hay otra copia abierta", self.port)
            return True
        self._server = srv
        threading.Thread(target=self._serve, name="akp03-instance", daemon=True).start()
        return True

    def _notify_other(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as conn:
                conn.sendall(HELLO)
                conn.settimeout(2)
                return conn.recv(len(ANSWER)) == ANSWER
        except OSError:
            return False

    def _serve(self) -> None:
        srv = self._server
        while srv is not None:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(2)
                    if conn.recv(len(HELLO)) == HELLO:
                        conn.sendall(ANSWER)
                        log.info("Se intentó abrir otra copia: se muestra esta ventana")
                        if self.on_show:
                            self.on_show()
                except OSError:
                    pass

    def release(self) -> None:
        srv, self._server = self._server, None
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
