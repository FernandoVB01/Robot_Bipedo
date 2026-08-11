#!/usr/bin/env python3
"""
=============================================================================
ROBOT BÍPEDO — Control de movimiento de la cámara Foscam (PTZ)
=============================================================================
Rol : Mover los motores de la Foscam FI8918W desde el código, para que la
      cámara "busque" al cliente en reposo y lo siga cuando levanta la mano.

¿Se mueve sola?  SÍ. La FI8918W no es una rótula manual: lleva dos motores
(pan horizontal ~270°, tilt vertical ~120°) que se comandan por HTTP. La misma
URL que usa su página web la podemos llamar nosotros:

    http://IP:PUERTO/decoder_control.cgi?command=N&user=USUARIO&pwd=CLAVE

Comandos (firmware MJPEG clásico de Foscam):
    0/1   arriba / parar arriba          4/5   izquierda / parar izquierda
    2/3   abajo  / parar abajo           6/7   derecha    / parar derecha
    25    centrar
    26/27 patrulla vertical   / parar    28/29 patrulla horizontal / parar

Añadiendo "&onestep=1" el motor da UN paso corto en vez de arrancar en continuo
(que obliga a mandar el comando de parada después). Para seguir una mano, pasos
sueltos es mucho más fácil de controlar.

Diseño — por qué hay un hilo:
    Cada comando es una petición HTTP a una cámara lenta de 2010, que puede
    tardar cientos de ms o directamente no contestar. Llamarla desde el bucle
    de la GUI congelaría la pantalla. Aquí se encola la última orden y un hilo
    aparte la ejecuta; si llegan órdenes más rápido de lo que la cámara puede
    obedecer, se descarta la vieja y se manda solo la última (es lo correcto:
    interesa dónde está la mano AHORA, no dónde estaba).

Si la cámara no responde o ptz.activo=false, todos los métodos son no-ops:
el robot sigue funcionando igual, solo que con la cámara quieta.
=============================================================================
"""

import json
import time
import threading
import urllib.parse
import urllib.request
from pathlib import Path

_CFG_PATH = Path(__file__).parent.parent / "config.json"
try:
    _CFG = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
except Exception:
    _CFG = {}

_P = _CFG.get("ptz", {})

# Comandos del firmware
CMD_ARRIBA, CMD_ABAJO       = 0, 2
CMD_IZQUIERDA, CMD_DERECHA  = 4, 6
CMD_CENTRAR                 = 25
CMD_PATRULLA_H, CMD_PARAR_PATRULLA_H = 28, 29


class FoscamPTZ:
    """Mueve la Foscam. Tolerante a fallos: nunca lanza hacia el llamador."""

    def __init__(self):
        self.activo      = _P.get("activo", True)
        self._base       = _P.get("base_url", "").rstrip("/")
        self._user       = _P.get("usuario", "admin")
        self._pwd        = _P.get("clave", "")
        self._timeout    = _P.get("timeout_s", 2.0)
        self._min_gap    = _P.get("intervalo_min_s", 0.45)
        self._zona_muerta = _P.get("zona_muerta", 0.18)
        self._inv_pan    = _P.get("invertir_pan", False)
        self._inv_tilt   = _P.get("invertir_tilt", False)
        self._seguir_tilt = _P.get("seguir_vertical", True)

        self._pendiente  = None            # Última orden sin ejecutar
        self._lock       = threading.Lock()
        self._despierta  = threading.Event()
        self._corriendo  = False
        self._hilo       = None
        self._ultimo_envio = 0.0
        self._fallos     = 0

        if self.activo and not self._base:
            self.activo = False
            print("[PTZ] Sin ptz.base_url en config.json — cámara fija.")

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def start(self):
        if not self.activo:
            print("[PTZ] Desactivado — la cámara no se moverá.")
            return
        self._corriendo = True
        self._hilo = threading.Thread(target=self._loop, daemon=True,
                                      name="FoscamPTZThread")
        self._hilo.start()
        print(f"[PTZ] Control de cámara activo → {self._base}")
        self.centrar()

    def stop(self):
        self._corriendo = False
        self._despierta.set()
        if self._hilo:
            self._hilo.join(timeout=3)

    # ── API que usa la GUI ───────────────────────────────────────────────────

    def centrar(self):
        """Vuelve al centro mecánico. Útil al arrancar y al terminar una venta."""
        self._encolar(CMD_CENTRAR, onestep=False)

    def patrulla(self, encender: bool):
        """
        Barrido horizontal continuo, el 'modo atracción' de la cámara: en reposo
        la cabeza va y viene buscando gente.
        """
        self._encolar(CMD_PATRULLA_H if encender else CMD_PARAR_PATRULLA_H,
                      onestep=False)

    def seguir(self, x, y):
        """
        Apunta la cámara hacia la mano. (x, y) vienen normalizados 0.0-1.0 desde
        el servidor de visión, con (0.5, 0.5) = centro de la imagen.

        Solo se mueve si la mano está fuera de la zona muerta central; sin ese
        margen la cámara oscilaría sin parar alrededor del centro.
        """
        if not self.activo or x is None:
            return

        dx = x - 0.5
        if abs(dx) > self._zona_muerta:
            # La imagen se mueve al revés que la cámara: si la mano aparece a la
            # derecha del encuadre, hay que girar la cámara a la derecha para
            # centrarla. Con invertir_pan=true se corrige si tu cámara va al revés.
            derecha = (dx > 0) != self._inv_pan
            self._encolar(CMD_DERECHA if derecha else CMD_IZQUIERDA)
            return

        if self._seguir_tilt and y is not None:
            dy = y - 0.5
            if abs(dy) > self._zona_muerta:
                abajo = (dy > 0) != self._inv_tilt
                self._encolar(CMD_ABAJO if abajo else CMD_ARRIBA)

    # ── Interno ──────────────────────────────────────────────────────────────

    def _encolar(self, command: int, onestep: bool = True):
        if not self.activo:
            return
        with self._lock:
            self._pendiente = (command, onestep)
        self._despierta.set()

    def _loop(self):
        while self._corriendo:
            self._despierta.wait(timeout=0.5)
            self._despierta.clear()

            with self._lock:
                orden = self._pendiente
                self._pendiente = None
            if orden is None:
                continue

            # Respetar el ritmo máximo: la mecánica es lenta y saturarla de
            # peticiones hace que se atasque a media pasada.
            espera = self._min_gap - (time.time() - self._ultimo_envio)
            if espera > 0:
                time.sleep(espera)

            self._enviar(*orden)

    def _enviar(self, command: int, onestep: bool):
        params = {"command": command, "user": self._user, "pwd": self._pwd}
        if onestep:
            params["onestep"] = 1
        url = f"{self._base}/decoder_control.cgi?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as r:
                r.read(64)
            self._ultimo_envio = time.time()
            self._fallos = 0
        except Exception as exc:
            self._fallos += 1
            if self._fallos == 1 or self._fallos % 20 == 0:
                print(f"[PTZ] No se pudo mover la cámara ({exc}).")
            if self._fallos >= 40:
                # La cámara no contesta: dejar de insistir para no penalizar
                # el resto del sistema. El robot sigue con la cámara quieta.
                self.activo = False
                print("[PTZ] Demasiados fallos — control de cámara desactivado.")
            self._ultimo_envio = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Prueba manual:  python3 pi/foscam_ptz.py
# Recorre los cuatro sentidos y vuelve al centro. Sirve para comprobar el
# cableado y las credenciales antes de arrancar el robot entero.
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ptz = FoscamPTZ()
    if not ptz.activo:
        raise SystemExit("PTZ desactivado o sin base_url en config.json")
    ptz.start()
    for nombre, cmd in [("derecha", CMD_DERECHA), ("izquierda", CMD_IZQUIERDA),
                        ("arriba",  CMD_ARRIBA),  ("abajo",     CMD_ABAJO)]:
        print(f"  → {nombre} (3 pasos)")
        for _ in range(3):
            ptz._encolar(cmd)
            time.sleep(0.7)
    print("  → centrar")
    ptz.centrar()
    time.sleep(3)
    ptz.stop()
    print("Listo. Si la cámara no se movió: revisá ptz.base_url, usuario y clave.")
