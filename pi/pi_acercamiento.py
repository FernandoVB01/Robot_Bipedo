#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — Lazo de acercamiento a la persona
=============================================================================
Rol : Convierte "veo una persona ahí" en "andá hacia ella y frená enfrente".

      Entrada : el recuadro de la persona que devuelve la PC
                (persona_x = dónde está · persona_alto = cuán cerca)
      Salida  : comandos MOV:<v>:<w> al ESP32

      Es un controlador proporcional de dos ejes, corriendo a 10 Hz:
        · GIRO   (w): centrar a la persona en el encuadre
        · AVANCE (v): acercarse hasta que el recuadro llegue al alto objetivo

─────────────────────────────────────────────────────────────────────────────
POR QUÉ LA ALTURA DEL RECUADRO Y NO UN SENSOR DE DISTANCIA
─────────────────────────────────────────────────────────────────────────────
Porque el robot no tiene sensor de distancia. Y no hace falta: una persona
parada mide más o menos lo mismo, así que cuánto ocupa a lo alto del encuadre
es un indicador de distancia perfectamente utilizable. No da metros, y no
importa — lo que importa es frenar SIEMPRE a la misma distancia, y para eso
alcanza con un número repetible.

Calibrarlo son 30 segundos: ponete donde querés que el robot se detenga, mirá
el número que este módulo imprime, y copialo en config.json → acercamiento.
alto_objetivo. Listo.

─────────────────────────────────────────────────────────────────────────────
TRES SEGUROS QUE NO SE TOCAN
─────────────────────────────────────────────────────────────────────────────
1. PRIMERO ENCARAR, DESPUÉS AVANZAR. Si la persona está muy descentrada, el
   robot gira sin avanzar. Un robot que avanza mientras corrige el rumbo hace
   una curva y termina pasando de largo por al lado del cliente.

2. SI SE PIERDE LA PERSONA, SE FRENA. Sin esto, un parpadeo del detector deja
   al robot avanzando a ciegas hacia alguien.

3. TIEMPO MÁXIMO. Si en `timeout_s` no llegó, se rinde y vuelve a la ruta. Un
   robot empujando contra un obstáculo que la cámara no ve es cómo se queman
   los motores.
=============================================================================
"""

import json
import time
from pathlib import Path
from typing import Optional

_CFG_PATH = Path(__file__).parent.parent / "config.json"
try:
    _CFG = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
except Exception:
    _CFG = {}
_A = _CFG.get("acercamiento", {})


class Acercamiento:
    """
    Uso desde la GUI:

        ac = Acercamiento(uart)
        ac.iniciar()
        ...  # en cada vuelta del bucle principal:
        estado = ac.actualizar(resultado_vision)
        if estado == Acercamiento.LLEGO:   ...
    """

    BUSCANDO  = "buscando"    # todavía no ve a nadie
    GIRANDO   = "girando"     # encarando a la persona
    AVANZANDO = "avanzando"
    LLEGO     = "llego"       # está enfrente: frenado y listo para saludar
    PERDIDA   = "perdida"     # la persona se fue
    TIMEOUT   = "timeout"     # se rindió

    def __init__(self, uart=None):
        self.uart = uart

        # Alto del recuadro con el que consideramos que ya está enfrente.
        # 0.62 ≈ una persona a algo más de un metro con la Foscam a 640x480.
        # ES UN PUNTO DE PARTIDA: hay que calibrarlo (ver arriba).
        self.alto_objetivo = _A.get("alto_objetivo", 0.62)
        self.alto_tolerancia = _A.get("alto_tolerancia", 0.05)

        # Zona muerta de centrado: si la persona está dentro de esta franja
        # alrededor del centro, no se corrige. Sin ella el robot oscila
        # persiguiendo el centro exacto para siempre.
        self.zona_muerta_x = _A.get("zona_muerta_x", 0.12)

        # Ganancias del proporcional
        self.kp_giro   = _A.get("kp_giro", 0.9)
        self.kp_avance = _A.get("kp_avance", 1.2)

        # Límites de velocidad. Bajos a propósito: el robot se acerca a una
        # PERSONA. Que tarde un segundo más da igual; que la embista, no.
        self.v_max = _A.get("v_max", 0.14)
        self.w_max = _A.get("w_max", 0.18)
        self.v_min = _A.get("v_min", 0.06)   # por debajo de esto no arranca

        # Si el error de centrado supera esto, primero gira y no avanza.
        self.umbral_encarar = _A.get("umbral_encarar", 0.22)

        self.timeout_s      = _A.get("timeout_s", 12.0)
        self.perdida_s      = _A.get("perdida_s", 1.2)
        self.confirmar_s    = _A.get("confirmar_s", 0.4)

        self.estado = self.BUSCANDO
        self._t_inicio    = 0.0
        self._t_ultima_ok = 0.0
        self._t_en_rango  = 0.0
        self._log_ts      = 0.0

    # ── Ciclo ────────────────────────────────────────────────────────────────

    def iniciar(self):
        """Arranca el acercamiento. La GUI lo llama al detectar a alguien."""
        self.estado       = self.BUSCANDO
        self._t_inicio    = time.time()
        self._t_ultima_ok = time.time()
        self._t_en_rango  = 0.0
        if self.uart:
            self.uart.modo("AUTO")
        print("[ACERCAMIENTO] Iniciado.")

    def cancelar(self):
        self._frenar()
        self.estado = self.PERDIDA

    def actualizar(self, vision: Optional[dict]) -> str:
        """
        Un paso del lazo. Llamar en cada vuelta del bucle de la GUI.
        `vision` es el último resultado de la PC. Devuelve el estado.
        """
        if self.estado in (self.LLEGO, self.TIMEOUT, self.PERDIDA):
            return self.estado

        ahora = time.time()

        # ── Seguro 3: tiempo máximo ──────────────────────────────────────
        if ahora - self._t_inicio > self.timeout_s:
            self._frenar()
            self.estado = self.TIMEOUT
            print("[ACERCAMIENTO] Se acabó el tiempo — vuelvo a la ruta.")
            return self.estado

        detectada = bool(vision and vision.get("persona_detectada"))
        x    = (vision or {}).get("persona_x")
        alto = (vision or {}).get("persona_alto")

        # ── Seguro 2: sin persona, se frena ──────────────────────────────
        if not detectada or x is None or alto is None:
            self._frenar()
            if ahora - self._t_ultima_ok > self.perdida_s:
                self.estado = self.PERDIDA
                print("[ACERCAMIENTO] Perdí a la persona.")
            else:
                self.estado = self.BUSCANDO
            return self.estado

        self._t_ultima_ok = ahora

        # ── ¿Ya llegó? ───────────────────────────────────────────────────
        if alto >= (self.alto_objetivo - self.alto_tolerancia):
            # Se pide que se mantenga en rango un ratito antes de dar por
            # buena la llegada: una detección suelta demasiado grande (un
            # brazo cerca de la cámara) no debe frenar el robot antes de tiempo.
            if self._t_en_rango == 0.0:
                self._t_en_rango = ahora
            elif ahora - self._t_en_rango >= self.confirmar_s:
                self._frenar()
                self.estado = self.LLEGO
                print(f"[ACERCAMIENTO] Llegué (alto={alto:.2f}).")
                return self.estado
        else:
            self._t_en_rango = 0.0

        # ── Control ──────────────────────────────────────────────────────
        error_x = x - 0.5                          # + = está a la derecha
        error_d = self.alto_objetivo - alto        # + = todavía lejos

        # Giro
        if abs(error_x) < self.zona_muerta_x:
            w = 0.0
        else:
            signo = 1.0 if error_x > 0 else -1.0
            magnitud = (abs(error_x) - self.zona_muerta_x) * self.kp_giro
            w = signo * min(magnitud, self.w_max)

        # Avance — seguro 1: si está muy descentrada, primero encarar
        if abs(error_x) > self.umbral_encarar:
            v = 0.0
            self.estado = self.GIRANDO
        else:
            v = min(max(error_d * self.kp_avance, 0.0), self.v_max)
            # Un duty por debajo del mínimo no vence la fricción: solo hace
            # zumbar los motores sin mover el robot.
            if 0.0 < v < self.v_min:
                v = self.v_min
            self.estado = self.AVANZANDO

        if self.uart:
            self.uart.mov(v, w)

        # Traza para calibrar `alto_objetivo` sin instrumental: pararse a la
        # distancia deseada y leer el número.
        if ahora - self._log_ts > 0.5:
            self._log_ts = ahora
            print(f"[ACERCAMIENTO] x={x:.2f} alto={alto:.2f} "
                  f"(objetivo {self.alto_objetivo:.2f}) → v={v:.2f} w={w:.2f}")

        return self.estado

    # ── Interno ──────────────────────────────────────────────────────────────

    def _frenar(self):
        if self.uart:
            self.uart.mov(0.0, 0.0)
            self.uart.parar()


# ─────────────────────────────────────────────────────────────────────────────
# PRUEBA SIN HARDWARE:  python3 pi_acercamiento.py
#
# Simula a una persona que aparece descentrada y lejos, y se comprueba que el
# robot primero la encara, después avanza, y frena al llegar.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    class UARTFalso:
        def __init__(self): self.ultimo = (0.0, 0.0)
        def mov(self, v, w): self.ultimo = (round(v, 3), round(w, 3))
        def parar(self): self.ultimo = (0.0, 0.0)
        def modo(self, m): pass

    uart = UARTFalso()
    ac = Acercamiento(uart)
    ac.iniciar()

    # La persona arranca lejos (alto 0.20) y muy a la derecha (x 0.85), y se va
    # acercando y centrando a medida que el robot corrige.
    x, alto = 0.85, 0.20
    print(f"\n{'t':>5} {'x':>5} {'alto':>5} {'estado':<10} {'v':>6} {'w':>6}")
    for paso in range(60):
        estado = ac.actualizar({"persona_detectada": True,
                                "persona_x": x, "persona_alto": alto})
        v, w = uart.ultimo
        if paso % 4 == 0 or estado == Acercamiento.LLEGO:
            print(f"{paso*0.1:5.1f} {x:5.2f} {alto:5.2f} {estado:<10} {v:6.2f} {w:6.2f}")
        if estado == Acercamiento.LLEGO:
            print("\n✔ Frenó enfrente de la persona.")
            break
        # Modelo grosero de la planta: girar centra, avanzar acerca.
        x    -= w * 0.35
        alto += v * 0.30
        time.sleep(0.01)
    else:
        print("\n✘ No llegó en 60 pasos — revisar ganancias.")

    print("\nComprobación de que se frena al perder a la persona:")
    ac2 = Acercamiento(uart)
    ac2.iniciar()
    ac2.actualizar({"persona_detectada": True, "persona_x": 0.5, "persona_alto": 0.3})
    print(f"  con persona → v,w = {uart.ultimo}")
    ac2.actualizar({"persona_detectada": False})
    print(f"  sin persona → v,w = {uart.ultimo}   (debe ser 0.0, 0.0)")
