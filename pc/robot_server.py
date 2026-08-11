#!/usr/bin/env python3
"""
=============================================================================
ROBOT BÍPEDO — Servidor todo-en-uno para la PC
=============================================================================
Levanta en UN SOLO proceso lo que antes eran dos ventanas de terminal:

    pc_server.py   → servidor de visión ZeroMQ (MediaPipe + QR)   :5555
    api_server.py  → API REST + dashboard web (FastAPI)           :8000

La Raspberry NO ejecuta esto: la Pi captura de la Foscam, comprime y manda los
frames por WiFi; aquí es donde corre la visión por computadora, que es lo que
la Pi no aguanta.

¿Por qué juntarlos?
  1. Una sola ventana que abrir y vigilar en vez de dos.
  2. MediaPipe se carga una vez sola en memoria (son ~200 MB).
  3. Corriendo separados, api_server no lograba importar las estadísticas
     vivas del servidor ZeroMQ y /api/server/stats devolvía un texto de
     relleno. Juntos, el dashboard muestra fps y detecciones de verdad.

Uso (en la PC, con la PC conectada al hotspot del celular):
    cd pc
    py robot_server.py

El dashboard queda en  http://localhost:8000  y, desde la Raspberry o el
celular, en  http://<ip-de-la-pc>:8000

Si preferís las dos ventanas de siempre, esto no estorba: seguí usando
`py pc_server.py` y `py api_server.py` por separado como hasta ahora.
=============================================================================
"""

import sys
import json
import threading
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
_RAIZ = _AQUI.parent
sys.path.insert(0, str(_AQUI))     # database, pc_server, api_server, firebase_client

import uvicorn


def _puerto_api() -> int:
    try:
        cfg = json.loads((_RAIZ / "config.json").read_text(encoding="utf-8"))
        return cfg.get("red", {}).get("api_puerto", 8000)
    except Exception:
        return 8000


def _mis_ips() -> list:
    """IPs locales de esta PC, para saber cuál poner en config.json de la Pi."""
    import socket
    try:
        nombre = socket.gethostname()
        return sorted({d[4][0] for d in socket.getaddrinfo(nombre, None)
                       if d[0] == socket.AF_INET})
    except Exception:
        return []


def main():
    print("=" * 70)
    print("  ROBOT BÍPEDO — servidor de visión + API (PC)")
    print("=" * 70)

    # Importar aquí (no arriba) para que los mensajes de arranque de MediaPipe,
    # SQLite y Firebase salgan después de la cabecera y se lean en orden.
    import pc_server
    from api_server import app

    # El servidor de visión va en un hilo: es un bucle bloqueante de ZeroMQ.
    hilo_vision = threading.Thread(target=pc_server.run_server, daemon=True,
                                   name="ZMQVisionServer")
    hilo_vision.start()

    puerto = _puerto_api()
    ips = _mis_ips()
    if ips:
        print(f"\n[PC] IP de esta PC: {', '.join(ips)}")
        print(f"[PC] Poné esa IP en config.json → red.pc_ip (la de 192.168.x.x "
              f"del hotspot)")
    print(f"[PC] Dashboard  → http://localhost:{puerto}")
    print(f"[PC] Swagger    → http://localhost:{puerto}/docs")
    print("[PC] Ctrl+C para detener.\n")

    try:
        # Se pasa el objeto app, no la cadena "api_server:app": con la cadena
        # uvicorn reimportaría el módulo y MediaPipe se cargaría dos veces.
        uvicorn.run(app, host="0.0.0.0", port=puerto, log_level="warning")
    except KeyboardInterrupt:
        print("\n[PC] Detenido.")


if __name__ == "__main__":
    main()
