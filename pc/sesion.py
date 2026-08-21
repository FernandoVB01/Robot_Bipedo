"""
=============================================================================
ROBOT PINGÜINO — Store de sesiones (el punto de encuentro celular ↔ robot)
=============================================================================
Rol : Es "la base de datos" del flujo nuevo. El celular del cliente escribe acá
      (cédula, joystick, producto elegido) y la Raspberry consulta acá, 20 veces
      por segundo, hasta ver un cambio.

¿Por qué vive en la PC y no en Firebase?
      Porque por acá pasa el MANEJO del robot. Contra Firebase cada comando sube
      y baja de internet: 200-500 ms. Al soltar el acelerador el robot seguiría
      medio segundo más. Contra la PC, que está en el mismo hotspot que el
      celular y que la Pi, el viaje completo es de ~30 ms.
      Las VENTAS sí se siguen espejando a Firebase, igual que hasta ahora: eso
      es persistencia, no control, y medio segundo ahí no le molesta a nadie.

Dos decisiones que evitan accidentes:

  1. EL CONTROL ES "ÚLTIMO VALOR GANA", NO UNA COLA.
     Si el WiFi hipa y llegan cinco posiciones del joystick juntas, el robot
     tiene que obedecer la última, no ejecutar las cinco en fila. Un acelerador
     viejo es peor que ningún acelerador.

  2. LAS ACCIONES SÍ SON UNA COLA.
     "Que baile", "siguiente producto" son eventos discretos: si el cliente
     aprieta dos veces, tienen que pasar las dos cosas. Se consumen una sola vez.

Dependencias: solo la biblioteca estándar.
=============================================================================
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# PARÁMETROS
# ─────────────────────────────────────────────────────────────────────────────

# Cuánto vive una sesión sin que nadie la toque. Si el cliente escanea el QR y
# se va, el robot no puede quedar secuestrado para siempre.
TTL_SIN_USO_S = 90.0

# Una sesión completa tampoco puede durar eternamente.
TTL_MAXIMO_S = 600.0

# Después de esto, un comando de joystick se considera vencido. La Pi lo vuelve
# a chequear de su lado; acá se marca para que no haya dudas.
CONTROL_VIGENCIA_S = 0.5


# ─────────────────────────────────────────────────────────────────────────────
# FASES DE LA SESIÓN
# ─────────────────────────────────────────────────────────────────────────────

class Fase:
    """Fases por las que pasa una interacción. Strings simples a propósito: van
    tal cual en el JSON que consulta la Pi y en el que lee la WebApp."""
    ESPERANDO   = "esperando"    # QR en pantalla, nadie lo escaneó todavía
    CONECTADO   = "conectado"    # el celular abrió la WebApp
    VALIDADA    = "validada"     # cédula correcta → el cliente ya maneja
    COMPRANDO   = "comprando"    # eligió producto, confirmando
    FINALIZADA  = "finalizada"   # compra cerrada, se libera el control
    CANCELADA   = "cancelada"
    EXPIRADA    = "expirada"


# ─────────────────────────────────────────────────────────────────────────────
# VALIDACIÓN DE CÉDULA ECUATORIANA (algoritmo Módulo 10)
#
# OJO — esto NO existía. `DOCUMENTACION_PROYECTO.md` y `config.json` decían que
# estaba implementado en pi_gui_gpio.py, pero ahí solo se armaban 10 dígitos y
# se mandaban sin comprobar nada. Con los botones físicos se notaba poco; ahora
# que la cédula la escribe el cliente en su propio teléfono, hace falta de
# verdad.
# ─────────────────────────────────────────────────────────────────────────────

_COEFICIENTES = (2, 1, 2, 1, 2, 1, 2, 1, 2)


def validar_cedula(cedula: str) -> tuple[bool, str]:
    """
    Valida una cédula ecuatoriana con el algoritmo Módulo 10.

    Devuelve (válida, motivo). El motivo se le muestra al cliente en el celular,
    así que está redactado para que lo entienda una persona, no un programador.

    El algoritmo:
      1. Son 10 dígitos.
      2. Los dos primeros son la provincia: 01-24, o 30 (ciudadanos en el exterior).
      3. El tercero identifica el tipo: menor a 6 para personas naturales.
      4. A los primeros 9 dígitos se les aplican los coeficientes 2,1,2,1,2,1,2,1,2.
         Si un producto pasa de 9, se le resta 9.
      5. El dígito verificador (el décimo) es (10 - suma % 10) % 10.
    """
    cedula = (cedula or "").strip()

    if not cedula.isdigit():
        return False, "La cédula debe tener solo números."
    if len(cedula) != 10:
        return False, "La cédula debe tener 10 dígitos."

    provincia = int(cedula[:2])
    if not (1 <= provincia <= 24) and provincia != 30:
        return False, "Los dos primeros dígitos no corresponden a una provincia."

    if int(cedula[2]) >= 6:
        return False, "El tercer dígito no corresponde a una cédula de persona natural."

    suma = 0
    for digito, coef in zip(cedula[:9], _COEFICIENTES):
        producto = int(digito) * coef
        if producto > 9:
            producto -= 9
        suma += producto

    verificador = (10 - (suma % 10)) % 10
    if verificador != int(cedula[9]):
        return False, "La cédula no es válida. Revisá los números."

    return True, "ok"


# ─────────────────────────────────────────────────────────────────────────────
# LA SESIÓN
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Sesion:
    token: str
    creada: float
    tocada: float
    fase: str = Fase.ESPERANDO
    seq: int = 0                       # sube en CADA cambio; así la Pi sabe si mirar

    cedula: Optional[str] = None
    producto_id: Optional[int] = None
    qr_codigo: Optional[str] = None
    transaccion: Optional[dict] = None

    # Consigna de manejo: último valor gana
    v: float = 0.0
    w: float = 0.0
    control_ts: float = 0.0

    # Acciones discretas pendientes de que la Pi las consuma
    acciones: deque = field(default_factory=deque)

    mensaje: str = ""                  # texto para mostrar en la pantalla del robot

    def a_dict(self, incluir_acciones: bool = False) -> dict:
        ahora = time.time()
        d = {
            "token":       self.token,
            "seq":         self.seq,
            "fase":        self.fase,
            "cedula":      self.cedula,
            "producto_id": self.producto_id,
            "mensaje":     self.mensaje,
            "control": {
                "v": self.v,
                "w": self.w,
                "edad_s": round(ahora - self.control_ts, 3) if self.control_ts else None,
                "vigente": bool(self.control_ts
                                and (ahora - self.control_ts) <= CONTROL_VIGENCIA_S),
            },
            "edad_s": round(ahora - self.creada, 1),
        }
        if incluir_acciones:
            d["acciones"] = []
        return d


# ─────────────────────────────────────────────────────────────────────────────
# EL STORE
# ─────────────────────────────────────────────────────────────────────────────

class StoreSesiones:
    """
    Guarda las sesiones vivas. Thread-safe: lo tocan el hilo de uvicorn (que
    atiende al celular) y el que atiende a la Pi.

    Solo hay UNA sesión activa por vez — es un robot, atiende a una persona.
    Abrir una nueva cancela la anterior, que es justo lo que se quiere si un
    cliente se va sin cerrar y llega el siguiente.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._sesiones: dict[str, Sesion] = {}
        self._activa: Optional[str] = None

    # ── Creación y consulta ──────────────────────────────────────────────

    def crear(self) -> Sesion:
        with self._lock:
            # token corto a propósito: cuanto más corta la URL, menos denso el
            # QR y más fácil de escanear en el panel de 480x320.
            token = secrets.token_urlsafe(6)
            ahora = time.time()
            s = Sesion(token=token, creada=ahora, tocada=ahora)

            if self._activa and self._activa in self._sesiones:
                anterior = self._sesiones[self._activa]
                if anterior.fase not in (Fase.FINALIZADA, Fase.EXPIRADA):
                    anterior.fase = Fase.CANCELADA
                    anterior.seq += 1

            self._sesiones[token] = s
            self._activa = token
            self._limpiar_viejas()
            return s

    def obtener(self, token: str) -> Optional[Sesion]:
        with self._lock:
            self._limpiar_viejas()
            return self._sesiones.get(token)

    def activa(self) -> Optional[Sesion]:
        with self._lock:
            if self._activa:
                return self._sesiones.get(self._activa)
            return None

    # ── Escrituras del celular ───────────────────────────────────────────

    def marcar_conectado(self, token: str) -> Optional[Sesion]:
        """El celular abrió la WebApp. La pantalla del robot lo refleja."""
        with self._lock:
            s = self._sesiones.get(token)
            if not s or s.fase != Fase.ESPERANDO:
                return s
            s.fase = Fase.CONECTADO
            s.tocada = time.time()
            s.seq += 1
            return s

    def set_cedula(self, token: str, cedula: str) -> tuple[bool, str, Optional[Sesion]]:
        with self._lock:
            s = self._sesiones.get(token)
            if not s:
                return False, "Sesión no encontrada o vencida.", None
            if s.fase in (Fase.FINALIZADA, Fase.CANCELADA, Fase.EXPIRADA):
                return False, "Esta sesión ya se cerró.", s

            ok, motivo = validar_cedula(cedula)
            if not ok:
                return False, motivo, s

            s.cedula = cedula
            s.fase   = Fase.VALIDADA
            s.tocada = time.time()
            s.seq   += 1
            return True, "ok", s

    def set_control(self, token: str, v: float, w: float) -> bool:
        """
        Consigna del joystick. ÚLTIMO VALOR GANA: se pisa lo anterior.

        No mueve `seq` a propósito. Si lo hiciera, la Pi vería "algo cambió" diez
        veces por segundo y el mecanismo de detección de cambios no serviría para
        nada. El control se lee aparte, siempre.
        """
        with self._lock:
            s = self._sesiones.get(token)
            if not s or s.fase not in (Fase.VALIDADA, Fase.COMPRANDO):
                return False
            s.v = max(-1.0, min(1.0, float(v)))
            s.w = max(-1.0, min(1.0, float(w)))
            s.control_ts = time.time()
            s.tocada = s.control_ts
            return True

    def push_accion(self, token: str, accion: str, datos: Optional[dict] = None) -> bool:
        """Evento discreto del celular (BAILE, PRODUCTO, SIGUIENTE...). Se encola
        porque cada apretón del cliente tiene que producir su efecto."""
        with self._lock:
            s = self._sesiones.get(token)
            if not s or s.fase in (Fase.FINALIZADA, Fase.CANCELADA, Fase.EXPIRADA):
                return False
            if len(s.acciones) > 20:        # alguien apretando como loco
                s.acciones.popleft()
            s.acciones.append({"accion": accion, "datos": datos or {},
                               "ts": time.time()})
            s.tocada = time.time()
            s.seq += 1
            return True

    def set_producto(self, token: str, producto_id: int,
                     qr_codigo: Optional[str] = None) -> bool:
        with self._lock:
            s = self._sesiones.get(token)
            if not s or s.fase not in (Fase.VALIDADA, Fase.COMPRANDO):
                return False
            s.producto_id = producto_id
            s.qr_codigo   = qr_codigo
            s.fase        = Fase.COMPRANDO
            s.tocada      = time.time()
            s.seq        += 1
            return True

    def finalizar(self, token: str, transaccion: Optional[dict] = None,
                  mensaje: str = "") -> Optional[Sesion]:
        with self._lock:
            s = self._sesiones.get(token)
            if not s:
                return None
            s.transaccion = transaccion
            s.mensaje     = mensaje
            s.fase        = Fase.FINALIZADA
            s.v = s.w = 0.0            # suelta el acelerador al cerrar
            s.control_ts  = 0.0
            s.tocada      = time.time()
            s.seq        += 1
            return s

    def cancelar(self, token: str, motivo: str = "") -> Optional[Sesion]:
        with self._lock:
            s = self._sesiones.get(token)
            if not s:
                return None
            s.fase = Fase.CANCELADA
            s.mensaje = motivo
            s.v = s.w = 0.0
            s.control_ts = 0.0
            s.seq += 1
            return s

    # ── Lectura de la Pi ─────────────────────────────────────────────────

    def estado(self, token: str, desde_seq: int = -1) -> Optional[dict]:
        """
        Lo que consulta la Raspberry 20 veces por segundo.

        Devuelve siempre el control (que cambia todo el tiempo y no mueve `seq`)
        y, si hubo cambios respecto de `desde_seq`, también las acciones
        pendientes — que se CONSUMEN acá: se entregan una sola vez.
        """
        with self._lock:
            s = self._sesiones.get(token)
            if not s:
                return None

            d = s.a_dict()
            d["cambio"] = (s.seq != desde_seq)

            if s.seq != desde_seq and s.acciones:
                d["acciones"] = list(s.acciones)
                s.acciones.clear()
            else:
                d["acciones"] = []

            return d

    # ── Mantenimiento ────────────────────────────────────────────────────

    def _limpiar_viejas(self):
        """Marca vencidas las sesiones abandonadas y borra las muy viejas.
        Se llama desde dentro del lock."""
        ahora = time.time()
        a_borrar = []
        for tok, s in self._sesiones.items():
            viva = s.fase not in (Fase.FINALIZADA, Fase.CANCELADA, Fase.EXPIRADA)
            if viva and (ahora - s.tocada > TTL_SIN_USO_S
                         or ahora - s.creada > TTL_MAXIMO_S):
                s.fase = Fase.EXPIRADA
                s.v = s.w = 0.0
                s.control_ts = 0.0
                s.seq += 1
            # Se conservan un rato después de cerradas para que el celular
            # alcance a ver la pantalla de "gracias".
            if ahora - s.tocada > TTL_MAXIMO_S:
                a_borrar.append(tok)
        for tok in a_borrar:
            self._sesiones.pop(tok, None)
            if self._activa == tok:
                self._activa = None


# Instancia única que importan api_server.py y quien la necesite.
store = StoreSesiones()


# ─────────────────────────────────────────────────────────────────────────────
# PRUEBA RÁPIDA:  python sesion.py
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("── Módulo 10 ─────────────────────────────────────────")
    # Casos construidos con el propio algoritmo + errores típicos.
    pruebas = [
        ("1710034065", True),    # verificador correcto
        ("0926687856", True),
        ("1710034064", False),   # verificador cambiado
        ("9999999999", False),   # provincia inexistente
        ("1760034065", False),   # tercer dígito >= 6
        ("171003406",  False),   # 9 dígitos
        ("17100A4065", False),   # con letra
    ]
    fallos = 0
    for ced, esperado in pruebas:
        ok, motivo = validar_cedula(ced)
        marca = "OK " if ok == esperado else "MAL"
        if ok != esperado:
            fallos += 1
        print(f"  [{marca}] {ced:<12} → {ok!s:<5} {motivo}")

    print("\n── Ciclo de sesión ───────────────────────────────────")
    s = store.crear()
    print(f"  token creado: {s.token}")
    store.marcar_conectado(s.token)
    ok, motivo, _ = store.set_cedula(s.token, "1710034065")
    print(f"  cédula válida → {ok} ({motivo})")
    store.set_control(s.token, 0.5, -0.2)
    store.push_accion(s.token, "BAILE")

    est = store.estado(s.token, desde_seq=-1)
    print(f"  estado: fase={est['fase']} control={est['control']} "
          f"acciones={est['acciones']}")
    est2 = store.estado(s.token, desde_seq=est["seq"])
    print(f"  segunda consulta: cambio={est2['cambio']} "
          f"acciones={est2['acciones']}  ← se consumen una sola vez")

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} PRUEBAS FALLARON'}")
