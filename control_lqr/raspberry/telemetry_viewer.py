#!/usr/bin/env python3
"""
=============================================================================
ROBOT BALANCIN - Visor de telemetria en tiempo real (Pygame)
=============================================================================
Grafica en pantalla lo que emite la ESP32 y lo guarda en CSV.

Sirve para DOS cosas, y detecta sola cual esta recibiendo:

  1) ETAPA A (hoy, sin VESC): la ESP32 con el firmware del MPU6050 emite
        tiempo_ms,ax,ay,az,gx,gy,gz
     El visor calcula el pitch con filtro complementario y lo grafica.
     Sirve para verificar EJES, SIGNO, BIAS y FRECUENCIA reales.

  2) ETAPAS D-H (con el firmware de balanceo): la ESP32 emite
        T,tiempo_ms,pitch_deg,gyro_rads,u_nm,i_a,erpm,estado
     El visor grafica el angulo y el esfuerzo de control.

Funciona igual en la Raspberry (UART /dev/ttyAMA0) y en la PC (COM7).

Uso:
    python telemetry_viewer.py --port COM7
    python telemetry_viewer.py --port /dev/ttyAMA0 --fullscreen

Dependencias:  pip install pygame pyserial numpy

Teclas:
    SHIFT+A  armar (START)        X  PARAR (STOP)  <- tecla de panico
    T  telemetria on/off          E  fijar cero de pitch (EQ)
    C  captura en rafaga          L  iniciar/parar log CSV
    P  pausar la grafica          R  limpiar buffers
    Q / ESC  salir (manda STOP antes de cerrar)

Seguridad: al salir, al cerrar la ventana o ante cualquier excepcion, el
visor envia STOP. Nunca arma solo.
=============================================================================
"""

import argparse
import csv
import math
import os
import queue
import sys
import threading
import time
from collections import deque
from datetime import datetime

try:
    import serial
except ImportError:
    sys.exit("Falta pyserial:  pip install pyserial")

try:
    import pygame
except ImportError:
    sys.exit("Falta pygame:  pip install pygame")


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACION
# ─────────────────────────────────────────────────────────────────────────────
VENTANA_S = 10.0          # segundos visibles en la grafica
FPS = 60
COLOR_FONDO = (18, 18, 22)
COLOR_REJILLA = (44, 44, 52)
COLOR_TEXTO = (225, 225, 232)
COLOR_TENUE = (140, 140, 152)
COLOR_OK = (120, 220, 140)
COLOR_ALERTA = (245, 180, 90)
COLOR_ERROR = (240, 110, 110)
COLOR_TRAZA = [(120, 190, 255), (255, 170, 90), (150, 230, 150)]


# ─────────────────────────────────────────────────────────────────────────────
# FILTRO COMPLEMENTARIO
# ─────────────────────────────────────────────────────────────────────────────
class FiltroComplementario:
    """Fusiona el angulo del acelerometro (lento pero sin deriva) con la
    integral del giroscopio (rapida pero con deriva).

        pitch = a*(pitch + gyro*dt) + (1-a)*pitch_acc,   a = tau/(tau+dt)
    """

    def __init__(self, tau=0.5):
        self.tau = tau
        self.pitch = None

    def actualizar(self, pitch_acc, gyro, dt):
        if self.pitch is None or dt <= 0 or dt > 0.5:
            self.pitch = pitch_acc          # arranque o hueco largo: confia en el acel
            return self.pitch
        a = self.tau / (self.tau + dt)
        self.pitch = a * (self.pitch + gyro * dt) + (1.0 - a) * pitch_acc
        return self.pitch

    def reset(self):
        self.pitch = None


def pitch_desde_acel(ax, ay, az, modo):
    """Angulo de inclinacion a partir de la gravedad medida.

    El montaje real de la IMU se confirma en la Etapa A; por eso el modo
    es configurable. Convencion del modelo: pitch > 0 = inclinado hacia
    adelante.
    """
    if modo == "xz":
        return math.atan2(-ax, az)
    if modo == "xz_inv":
        return math.atan2(ax, az)
    if modo == "yz":
        return math.atan2(-ay, az)
    if modo == "yz_inv":
        return math.atan2(ay, az)
    raise ValueError("modo de pitch desconocido: %s" % modo)


# ─────────────────────────────────────────────────────────────────────────────
# LECTOR SERIE (hilo aparte)
# ─────────────────────────────────────────────────────────────────────────────
class LectorSerie(threading.Thread):
    """Lee lineas del puerto y las convierte en muestras.

    Corre en su propio hilo: la grafica nunca se bloquea esperando datos.
    Si el puerto se cae, reintenta abrirlo solo.
    """

    def __init__(self, puerto, baud, modo_pitch, tau):
        super().__init__(daemon=True)
        self.puerto, self.baud = puerto, baud
        self.modo_pitch = modo_pitch
        self.filtro = FiltroComplementario(tau)
        self.muestras = queue.Queue(maxsize=20000)
        self.mensajes = queue.Queue(maxsize=200)
        self.tx = queue.Queue(maxsize=100)
        self.corriendo = True
        self.conectado = False
        self.descartadas = 0
        self.formato = "?"
        self._t_ant = None
        self._ser = None

    # -- API publica ------------------------------------------------------
    def enviar(self, comando):
        try:
            self.tx.put_nowait(comando)
        except queue.Full:
            pass

    def detener(self):
        self.corriendo = False

    # -- interno ----------------------------------------------------------
    def _nota(self, texto):
        try:
            self.mensajes.put_nowait((time.time(), texto))
        except queue.Full:
            pass

    def run(self):
        while self.corriendo:
            try:
                self._ser = serial.Serial(self.puerto, self.baud, timeout=0.2)
                self.conectado = True
                self._nota("Conectado a %s @ %d" % (self.puerto, self.baud))
                self._bucle()
            except serial.SerialException as e:
                self.conectado = False
                self._nota("Sin puerto (%s). Reintentando..." % e)
                time.sleep(1.5)
            finally:
                if self._ser is not None:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
                    self._ser = None

    def _bucle(self):
        while self.corriendo:
            # 1) mandar lo pendiente
            while not self.tx.empty():
                cmd = self.tx.get_nowait()
                self._ser.write((cmd + "\n").encode("ascii", "ignore"))

            # 2) leer una linea
            cruda = self._ser.readline()
            if not cruda:
                continue
            linea = cruda.decode("utf-8", "ignore").strip()
            if not linea:
                continue

            # 3) mensajes de estado (no son datos)
            if linea[0] == "#" or linea.startswith("ACK:") or linea.startswith("ERR:"):
                self._nota(linea)
                continue

            m = self._parsear(linea)
            if m is None:
                self.descartadas += 1
            else:
                try:
                    self.muestras.put_nowait(m)
                except queue.Full:
                    pass

    def _parsear(self, linea):
        """Devuelve dict de muestra o None si la linea no es valida."""
        partes = linea.replace(";", ",").split(",")

        # --- Formato de telemetria del firmware de balanceo ---------------
        # T,t_ms,pitch_deg,gyro,u_nm,i_a,erpm,estado
        if partes[0] == "T":
            if len(partes) < 8:
                return None
            try:
                t_ms = float(partes[1])
                pitch_deg = float(partes[2])
                gyro = float(partes[3])
                u_nm = float(partes[4])
                i_a = float(partes[5])
                erpm = float(partes[6])
                estado = partes[7].strip()
            except ValueError:
                return None
            self.formato = "balanceo"
            return {"t": t_ms / 1000.0, "pitch": pitch_deg, "gyro": gyro,
                    "u": u_nm, "i": i_a, "erpm": erpm, "estado": estado}

        # --- Formato crudo del MPU6050 (Etapa A) --------------------------
        # t_ms,ax,ay,az[,gx,gy,gz]
        try:
            v = [float(x) for x in partes]
        except ValueError:
            return None
        if len(v) not in (4, 7):
            return None

        t = v[0] / 1000.0
        ax, ay, az = v[1], v[2], v[3]
        gy = v[5] if len(v) == 7 else 0.0

        dt = 0.0 if self._t_ant is None else (t - self._t_ant)
        self._t_ant = t

        pitch_acc = pitch_desde_acel(ax, ay, az, self.modo_pitch)
        pitch = self.filtro.actualizar(pitch_acc, gy, dt)

        # Sin claves u/i/erpm a proposito: en esta etapa no existen todavia,
        # y asi el panel de control no dibuja trazas planas en cero.
        self.formato = "imu_cruda"
        return {"t": t, "pitch": math.degrees(pitch),
                "pitch_acc": math.degrees(pitch_acc), "gyro": gy,
                "ax": ax, "ay": ay, "az": az,
                "modulo": math.sqrt(ax * ax + ay * ay + az * az),
                "estado": "-"}


# ─────────────────────────────────────────────────────────────────────────────
# GRAFICA
# ─────────────────────────────────────────────────────────────────────────────
class Panel:
    """Un recuadro con trazas deslizantes y escala automatica."""

    def __init__(self, rect, titulo, series, minimo=1.0):
        self.rect = pygame.Rect(rect)
        self.titulo = titulo
        self.series = series              # [(clave, etiqueta), ...]
        self.minimo = minimo              # semiescala minima del eje Y

    ALTO_CABECERA = 24        # banda superior reservada al titulo y la leyenda

    def dibujar(self, sup, fuente, datos, t_fin):
        pygame.draw.rect(sup, (26, 26, 32), self.rect, border_radius=6)
        pygame.draw.rect(sup, COLOR_REJILLA, self.rect, width=1, border_radius=6)

        # Solo se listan (y escalan) las series que realmente traen datos:
        # en modo balanceo no llega 'pitch_acc' y no tiene sentido anunciarla.
        activas = [(c, e) for c, e in self.series
                   if any(m.get(c) is not None for m in datos)]

        pico = self.minimo
        for clave, _ in activas:
            for m in datos:
                v = m.get(clave)
                if v is not None:
                    pico = max(pico, abs(v))
        pico *= 1.15

        # zona de trazado por debajo de la cabecera
        arriba = self.rect.top + self.ALTO_CABECERA
        cy = (arriba + self.rect.bottom) / 2.0
        alto = (self.rect.bottom - arriba) / 2.0 - 6

        pygame.draw.line(sup, COLOR_REJILLA,
                         (self.rect.left + 1, cy), (self.rect.right - 1, cy))
        for frac in (1.0, -1.0):
            y = cy - frac * alto
            pygame.draw.line(sup, (34, 34, 42),
                             (self.rect.left + 1, y), (self.rect.right - 1, y))
            sup.blit(fuente.render("%+.1f" % (frac * pico), True, COLOR_TENUE),
                     (self.rect.left + 6, y - 7 if frac < 0 else y + 1))

        # trazas
        t_ini = t_fin - VENTANA_S
        for idx, (clave, etiqueta) in enumerate(activas):
            color = COLOR_TRAZA[idx % len(COLOR_TRAZA)]
            puntos = []
            for m in datos:
                v = m.get(clave)
                if v is None:
                    continue
                x = self.rect.left + (m["t"] - t_ini) / VENTANA_S * self.rect.width
                y = cy - max(-1.0, min(1.0, v / pico)) * alto
                puntos.append((x, y))
            if len(puntos) > 1:
                pygame.draw.lines(sup, color, False, puntos, 2)

        # leyenda en una sola fila, alineada a la derecha (nunca se desborda)
        x = self.rect.right - 10
        for idx in range(len(activas) - 1, -1, -1):
            color = COLOR_TRAZA[idx % len(COLOR_TRAZA)]
            img = fuente.render(activas[idx][1], True, color)
            x -= img.get_width()
            sup.blit(img, (x, self.rect.top + 5))
            x -= 14

        sup.blit(fuente.render(self.titulo, True, COLOR_TEXTO),
                 (self.rect.left + 8, self.rect.top + 5))


# ─────────────────────────────────────────────────────────────────────────────
# APLICACION
# ─────────────────────────────────────────────────────────────────────────────
class Visor:
    def __init__(self, args):
        self.args = args
        self.lector = LectorSerie(args.port, args.baud, args.pitch_mode, args.tau)
        self.datos = deque(maxlen=20000)
        self.notas = deque(maxlen=8)
        self.pausado = False
        self.t_vista = 0.0
        self.hz = 0.0
        self._marcas = deque(maxlen=120)

        self.csv_f = None
        self.csv_w = None

        pygame.init()
        pygame.display.set_caption("Robot balancin - telemetria")
        banderas = pygame.FULLSCREEN if args.fullscreen else 0
        self.pantalla = pygame.display.set_mode((args.width, args.height), banderas)
        self.reloj = pygame.time.Clock()
        self.f_chica = pygame.font.SysFont("consolas,dejavusansmono,monospace", 15)
        self.f_grande = pygame.font.SysFont("consolas,dejavusansmono,monospace", 30, bold=True)

        w, h = args.width, args.height
        alto_panel = int((h - 210) / 2)
        self.panel_ang = Panel((20, 150, w - 40, alto_panel),
                               "Pitch [grados]",
                               [("pitch", "pitch (filtrado)"),
                                ("pitch_acc", "pitch (acelerometro)")],
                               minimo=5.0)
        self.panel_ctl = Panel((20, 160 + alto_panel, w - 40, alto_panel),
                               "Esfuerzo de control / giro",
                               [("u", "torque [N.m]"),
                                ("i", "corriente [A]"),
                                ("gyro", "gyro [rad/s]")],
                               minimo=1.0)

    # -- log CSV ----------------------------------------------------------
    def alternar_log(self):
        if self.csv_f is not None:
            self.csv_f.close()
            self.csv_f = self.csv_w = None
            self.nota("Log CSV cerrado")
            return
        os.makedirs(self.args.logdir, exist_ok=True)
        nombre = os.path.join(
            self.args.logdir,
            "telemetria_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        self.csv_f = open(nombre, "w", newline="", encoding="utf-8")
        self.csv_w = csv.writer(self.csv_f)
        self.csv_w.writerow(["t_s", "pitch_deg", "pitch_acc_deg", "gyro_rads",
                             "u_nm", "i_a", "erpm", "estado"])
        self.nota("Log -> %s" % os.path.basename(nombre))

    def nota(self, texto):
        self.notas.append((time.time(), texto))

    # -- ciclo ------------------------------------------------------------
    def ejecutar(self):
        self.lector.start()
        try:
            while True:
                if not self.procesar_eventos():
                    break
                self.consumir_serie()
                self.dibujar()
                self.reloj.tick(FPS)
        finally:
            self.apagar()

    def procesar_eventos(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return False
            if ev.type != pygame.KEYDOWN:
                continue
            k = ev.key
            mods = pygame.key.get_mods()

            if k in (pygame.K_q, pygame.K_ESCAPE):
                return False
            elif k == pygame.K_x:
                self.lector.enviar("STOP")
                self.nota(">> STOP")
            elif k == pygame.K_a and (mods & pygame.KMOD_SHIFT):
                self.lector.enviar("START")
                self.nota(">> START (armado)")
            elif k == pygame.K_a:
                self.nota("Para armar: SHIFT+A (evita arranques accidentales)")
            elif k == pygame.K_t:
                self.tele = not getattr(self, "tele", False)
                self.lector.enviar("TELE_ON" if self.tele else "TELE_OFF")
                self.nota(">> TELE_%s" % ("ON" if self.tele else "OFF"))
            elif k == pygame.K_e:
                self.lector.enviar("EQ")
                self.nota(">> EQ (fijar cero de pitch)")
            elif k == pygame.K_c:
                self.lector.enviar("CAPTURA")
                self.nota(">> CAPTURA en rafaga")
            elif k == pygame.K_l:
                self.alternar_log()
            elif k == pygame.K_p:
                self.pausado = not self.pausado
                self.nota("Grafica %s" % ("pausada" if self.pausado else "activa"))
            elif k == pygame.K_r:
                self.datos.clear()
                self.lector.filtro.reset()
                self.nota("Buffers limpiados")
        return True

    def consumir_serie(self):
        n = 0
        while not self.lector.muestras.empty() and n < 2000:
            m = self.lector.muestras.get_nowait()
            n += 1
            self.datos.append(m)
            self._marcas.append(m["t"])
            if self.csv_w is not None:
                self.csv_w.writerow([
                    "%.4f" % m["t"], "%.4f" % m.get("pitch", 0.0),
                    "%.4f" % m.get("pitch_acc", 0.0), "%.5f" % m.get("gyro", 0.0),
                    "%.4f" % m.get("u", 0.0), "%.4f" % m.get("i", 0.0),
                    "%.1f" % m.get("erpm", 0.0), m.get("estado", "-")])
            if not self.pausado:
                self.t_vista = m["t"]

        if len(self._marcas) > 5:
            lapso = self._marcas[-1] - self._marcas[0]
            if lapso > 0:
                self.hz = (len(self._marcas) - 1) / lapso

        while not self.lector.mensajes.empty():
            _, texto = self.lector.mensajes.get_nowait()
            self.notas.append((time.time(), texto))

        # descarta lo que ya salio de la ventana visible
        limite = self.t_vista - VENTANA_S - 1.0
        while self.datos and self.datos[0]["t"] < limite:
            self.datos.popleft()

    def dibujar(self):
        p = self.pantalla
        p.fill(COLOR_FONDO)
        ult = self.datos[-1] if self.datos else {}

        # ---- cabecera: lecturas grandes ----
        img_pitch = self.f_grande.render(
            "PITCH  %+7.2f deg" % ult.get("pitch", 0.0), True, COLOR_TEXTO)
        p.blit(img_pitch, (24, 18))
        estado = ult.get("estado", "-")
        col_estado = {"ARMED": COLOR_OK, "FAULT": COLOR_ERROR,
                      "-": COLOR_TENUE}.get(estado, COLOR_ALERTA)
        p.blit(self.f_grande.render("[%s]" % estado, True, col_estado),
               (24 + img_pitch.get_width() + 40, 18))

        conn = "CONECTADO" if self.lector.conectado else "SIN PUERTO"
        col_conn = COLOR_OK if self.lector.conectado else COLOR_ERROR
        p.blit(self.f_chica.render("%s  %s @ %d" % (conn, self.args.port, self.args.baud),
                                   True, col_conn), (24, 58))

        info = "formato: %-10s   %6.1f Hz   descartadas: %d   %s" % (
            self.lector.formato, self.hz, self.lector.descartadas,
            "LOG ACTIVO" if self.csv_w else "sin log")
        p.blit(self.f_chica.render(info, True, COLOR_TENUE), (24, 78))

        if self.lector.formato == "imu_cruda":
            mod = ult.get("modulo", 0.0)
            col = COLOR_OK if abs(mod - 9.81) < 0.6 else COLOR_ALERTA
            extra = "|a| = %5.2f m/s2 (quieto debe dar ~9.81)   ax=%+6.2f ay=%+6.2f az=%+6.2f" % (
                mod, ult.get("ax", 0), ult.get("ay", 0), ult.get("az", 0))
            p.blit(self.f_chica.render(extra, True, col), (24, 98))
        else:
            extra = "torque %+6.3f N.m   corriente %+6.2f A   erpm %+8.0f" % (
                ult.get("u", 0.0), ult.get("i", 0.0), ult.get("erpm", 0.0))
            p.blit(self.f_chica.render(extra, True, COLOR_TENUE), (24, 98))

        ayuda = ("SHIFT+A armar   X PARAR   T telemetria   E cero   "
                 "C captura   L log   P pausa   R limpiar   Q salir")
        p.blit(self.f_chica.render(ayuda, True, COLOR_TENUE), (24, 120))

        # ---- paneles ----
        vista = [m for m in self.datos if m["t"] >= self.t_vista - VENTANA_S]
        self.panel_ang.dibujar(p, self.f_chica, vista, self.t_vista)
        self.panel_ctl.dibujar(p, self.f_chica, vista, self.t_vista)

        # ---- consola de mensajes ----
        y = self.args.height - 46
        for _, texto in list(self.notas)[-3:]:
            p.blit(self.f_chica.render(texto[:150], True, COLOR_TENUE), (24, y))
            y += 16

        pygame.display.flip()

    def apagar(self):
        # Seguridad: pase lo que pase, el robot se desarma al cerrar el visor.
        try:
            self.lector.enviar("STOP")
            time.sleep(0.25)
        except Exception:
            pass
        self.lector.detener()
        if self.csv_f is not None:
            self.csv_f.close()
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description="Visor de telemetria del robot balancin")
    ap.add_argument("--port", default="COM7",
                    help="COM7 en Windows, /dev/ttyAMA0 en la Raspberry")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--pitch-mode", default="xz",
                    choices=["xz", "xz_inv", "yz", "yz_inv"],
                    help="montaje de la IMU; se confirma en la Etapa A")
    ap.add_argument("--tau", type=float, default=0.5,
                    help="constante del filtro complementario [s]")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--logdir", default="logs")
    Visor(ap.parse_args()).ejecutar()


if __name__ == "__main__":
    main()
