#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — RECORRIDO (patrullaje por pasos)
=============================================================================
La Pi le manda al ESP32 los MISMOS caracteres que mandabas por WASD desde la
laptop (w/a/s/d/q/e/x/+/-), pero siguiendo un guion. Así el robot hace un
recorrido solo.

Firmware objetivo: el WASD con pulso de seguridad (cada tecla mueve ~pulso_ms
y frena solo). Por eso, para moverse un rato, se REENVÍA la tecla cada 0.25 s.
No hay odometría: el recorrido es por TIEMPO (dead reckoning), así que los
segundos de cada giro/avance se calibran a ojo (dependen de piso y batería).

CONEXIÓN (lo más simple): enchufá el USB del ESP32 a la Pi → aparece como
/dev/ttyUSB0 (o /dev/ttyACM0). Es el mismo puerto por el que lo manejabas
desde la laptop.

USO:
    # Probar la lógica SIN motores (no abre el puerto, solo imprime):
    python3 recorrido.py --dry

    # Ejecutar de verdad (ruedas en el aire la primera vez, kill switch a mano):
    python3 recorrido.py --puerto /dev/ttyUSB0

    # Repetir el recorrido en loop (patrullar). 0 = para siempre:
    python3 recorrido.py --loop 0

    # Usar otro archivo de pasos:
    python3 recorrido.py mi_ruta.json

Pasos disponibles (en el JSON):
    {"accion":"avanzar",   "seg":2.0}
    {"accion":"atras",     "seg":1.0}
    {"accion":"girar_izq", "seg":0.9}
    {"accion":"girar_der", "seg":0.9}
    {"accion":"curva_izq", "seg":1.5}
    {"accion":"curva_der", "seg":1.5}
    {"accion":"esperar",   "seg":2.0}
    {"accion":"frenar"}
    {"accion":"mas_rapido"}   ·   {"accion":"mas_lento"}
=============================================================================
"""

import argparse
import json
import sys
import time
from pathlib import Path

# accion → tecla del firmware WASD
TECLAS = {
    "avanzar": "w", "atras": "s",
    "girar_izq": "a", "girar_der": "d",
    "curva_izq": "q", "curva_der": "e",
    "frenar": "x",
    "mas_rapido": "+", "mas_lento": "-",
}
INSTANTANEAS = {"frenar", "mas_rapido", "mas_lento"}

REENVIO_S = 0.25   # cada cuánto se reenvía la tecla mientras dura un paso
BAUD      = 115200

# Recorrido por defecto: un cuadrado (calibrá los 'seg' de girar_der para ~90°)
RECORRIDO_DEFAULT = {
    "pasos": [
        {"accion": "avanzar",   "seg": 2.0},
        {"accion": "girar_der", "seg": 0.9},
        {"accion": "avanzar",   "seg": 2.0},
        {"accion": "girar_der", "seg": 0.9},
        {"accion": "avanzar",   "seg": 2.0},
        {"accion": "girar_der", "seg": 0.9},
        {"accion": "avanzar",   "seg": 2.0},
        {"accion": "girar_der", "seg": 0.9},
        {"accion": "esperar",   "seg": 1.0},
    ]
}


class Conductor:
    """Manda teclas al ESP32 (o las imprime, en dry-run)."""

    def __init__(self, puerto, dry):
        self.dry = dry
        self.ser = None
        if not dry:
            try:
                import serial
                self.ser = serial.Serial(puerto, BAUD, timeout=0.2)
                time.sleep(0.3)   # que el ESP32 salga de reset
                print(f"[RECORRIDO] Conectado a {puerto} @ {BAUD}")
            except Exception as e:
                print(f"[RECORRIDO] No pude abrir {puerto} ({e}). Paso a DRY-RUN.")
                self.dry = True

    def tecla(self, ch):
        if self.dry or self.ser is None:
            print(f"    → '{ch}'")
        else:
            self.ser.write(ch.encode("ascii"))

    def linea(self, s):
        """Manda un comando con Enter (F<cm>, G<grados>)."""
        if self.dry or self.ser is None:
            print(f"    → '{s}' + Enter")
        else:
            self.ser.write((s + "\n").encode("ascii"))

    def esperar_cumplido(self, timeout=10.0):
        """Espera a que el firmware avise 'objetivo cumplido' (fin del movimiento por odometría)."""
        if self.dry or self.ser is None:
            print("    (espera 'objetivo cumplido')")
            return
        fin = time.time() + timeout
        while time.time() < fin:
            try:
                ln = self.ser.readline().decode("utf-8", "ignore")
            except Exception:
                ln = ""
            if "cumplido" in ln.lower():
                return
        print("    (timeout esperando 'objetivo cumplido')")

    def frenar(self):
        self.tecla("x")

    def cerrar(self):
        try:
            self.frenar()
            if self.ser:
                self.ser.close()
        except Exception:
            pass


def ejecutar_paso(cond, paso):
    accion = paso.get("accion", "")

    # ── Movimientos por ODOMETRÍA (exactos): manda el comando y espera que termine ──
    if accion == "avanzar_cm":
        cm = float(paso.get("cm", 0))
        print(f"  avanzar {cm:g} cm (odometría)")
        cond.linea(f"U{cm:g}")
        cond.esperar_cumplido()
        return
    if accion == "girar_grados":
        g = float(paso.get("grados", 0))
        print(f"  girar {g:g}° (odometría)")
        cond.linea(f"I{g:g}")
        cond.esperar_cumplido()
        return

    if accion == "esperar":                # quieto, sin motor
        seg = float(paso.get("seg", 1.0))
        print(f"  esperar {seg:.1f}s")
        cond.frenar()
        time.sleep(seg)
        return

    if accion not in TECLAS:
        print(f"[RECORRIDO] Paso desconocido: {paso}")
        return
    tecla = TECLAS[accion]

    if accion in INSTANTANEAS:
        print(f"  {accion}")
        cond.tecla(tecla)
        return

    seg = float(paso.get("seg", 1.0))
    print(f"  {accion} {seg:.1f}s  (reenvía '{tecla}' cada {REENVIO_S:.2f}s)")
    fin = time.time() + seg
    while time.time() < fin:
        cond.tecla(tecla)
        time.sleep(REENVIO_S)
    cond.frenar()                          # frena al terminar el paso


def main():
    ap = argparse.ArgumentParser(description="Recorrido del robot por pasos (WASD).")
    ap.add_argument("ruta", nargs="?", help="archivo JSON con los pasos")
    ap.add_argument("--puerto", default="/dev/ttyUSB0", help="puerto serie del ESP32")
    ap.add_argument("--dry", action="store_true", help="no abre el puerto, solo imprime")
    ap.add_argument("--loop", type=int, default=1, help="cuántas veces (0 = para siempre)")
    args = ap.parse_args()

    if args.ruta and Path(args.ruta).exists():
        recorrido = json.loads(Path(args.ruta).read_text("utf-8"))
    else:
        if args.ruta:
            print(f"[RECORRIDO] No encontré {args.ruta}; uso el recorrido por defecto.")
        recorrido = RECORRIDO_DEFAULT
    pasos = recorrido.get("pasos", [])
    if not pasos:
        print("[RECORRIDO] El recorrido no tiene pasos."); return

    cond = Conductor(args.puerto, args.dry)
    print(f"[RECORRIDO] {len(pasos)} pasos · loop={'∞' if args.loop == 0 else args.loop}"
          f"{'  (DRY-RUN)' if cond.dry else ''}")
    print("[RECORRIDO] Ruedas en el aire la primera vez. Ctrl+C para frenar y salir.\n")

    vuelta = 0
    try:
        while args.loop == 0 or vuelta < args.loop:
            vuelta += 1
            print(f"── Vuelta {vuelta} ──")
            for paso in pasos:
                ejecutar_paso(cond, paso)
            cond.frenar()
    except KeyboardInterrupt:
        print("\n[RECORRIDO] Interrumpido — frenando.")
    finally:
        cond.cerrar()
        print("[RECORRIDO] Fin. Robot frenado.")


if __name__ == "__main__":
    main()
