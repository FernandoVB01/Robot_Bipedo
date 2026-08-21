#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — GUI principal (Pygame) · la cara y el cerebro del robot
=============================================================================
Entorno virtual : /home/pi/mi_proyecto_env
Activar         : source ~/mi_proyecto_env/bin/activate

Dependencias:
    pip install pygame numpy pyserial pyzmq opencv-python-headless gpiozero qrcode

Flujo de la interacción:

  IDLE ──persona detectada──► ACERCAMIENTO ──llegó──► INVITACION (QR en pantalla)
                                   │                        │
                              (se fue/timeout)         celular escanea
                                   │                        ▼
                                   │                  ESPERA_CEDULA
                                   │                        │ cédula válida
                                   │                        ▼
                                   │                    TELEOP  ← el cliente maneja
                                   │                        │      desde su teléfono
                                   │                        │ compra cerrada
                                   │                        ▼
                                   │                     FACTURA
                                   │                        ▼
                                   └──────────────────► DESPEDIDA (baile + giro)
                                                            │
                                                            ▼
                                                          IDLE

Qué cambió respecto de la versión anterior:

  · LA CÉDULA YA NO SE INGRESA CON BOTONES. Antes eran 10 dígitos con 4
    pulsadores — casi un minuto de trabajo para el cliente. Ahora escanea el QR
    y la escribe en su propio teclado. La pantalla de dígitos se eliminó.

  · EL QR CAMBIÓ DE LADO. Antes el cliente le mostraba un QR de papel a la
    cámara; ahora la pantalla genera el QR y el celular lo lee.

  · EL ROBOT VA HACIA LA PERSONA. Antes esperaba un gesto de mano; ahora la
    detecta, se acerca y frena enfrente.

  · HAY UN PINGÜINO. La pantalla es una cara, no un cartel.

Botones GPIO (siguen cableados, ahora para el OPERADOR, no para el cliente):
    [OK]  → saltear el estado actual (útil en demos)
    [DEL] → cancelar la interacción y volver a la ruta
=============================================================================
"""

import sys, os, math, time, random, threading, json
import urllib.request
import numpy as np
from enum import Enum, auto
from queue import Queue, Empty
from pathlib import Path

import pygame
import pygame.mixer
import pygame.font

# GPIO
try:
    from gpiozero import Button
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False

# Módulos del proyecto
#
# Cámara y UART se importan POR SEPARADO a propósito. Estaban en un mismo `try`,
# y eso significaba que si faltaba `pyserial` (o sea, si todavía no hay ESP32
# conectado y nadie lo instaló) el robot se quedaba también sin cámara, que no
# tiene nada que ver. Cada pieza se cae sola.
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

try:
    from pi_zmq_client import VisionClient
    VISION_OK = True
except ImportError as _e:
    VISION_OK = False
    print(f"[GUI] Sin cámara: {_e}")

try:
    from pi_uart import UARTController
    UART_OK = True
except ImportError as _e:
    UART_OK = False
    print(f"[GUI] Sin UART (¿falta pyserial, o todavía no hay ESP32?): {_e}")

from pinguino import Pinguino
from pi_qr import GeneradorQR
from pi_sesion_client import SesionClient
from pi_acercamiento import Acercamiento

# Movimiento de la cámara: opcional, el robot funciona igual sin él
try:
    from foscam_ptz import FoscamPTZ
    PTZ_OK = True
except ImportError:
    PTZ_OK = False

# ── Config ─────────────────────────────────────────────────────────────────
_CFG_PATH = _HERE.parent / "config.json"
try:
    _CFG = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
except Exception:
    _CFG = {}

# ROBOT_PC_IP pisa el config.json: la IP de la PC en el hotspot cambia seguido.
PC_IP         = os.environ.get("ROBOT_PC_IP") \
                or _CFG.get("red", {}).get("pc_ip", "192.168.43.50")
API_PORT      = _CFG.get("red", {}).get("api_puerto", 8000)
API_BASE      = f"http://{PC_IP}:{API_PORT}"

_R = _CFG.get("robot", {})
RODAR_VELOCIDAD = _R.get("rodar_velocidad", 0.10)
GIRO_FINAL      = _R.get("giro_final_grados", 180)

_S = _CFG.get("sesion", {})
T_INVITACION    = _S.get("timeout_invitacion_s", 45)
T_CEDULA        = _S.get("timeout_cedula_s", 90)
T_TELEOP        = _S.get("timeout_teleop_s", 240)
T_FACTURA       = _S.get("tiempo_factura_s", 12)
T_DESPEDIDA     = _S.get("tiempo_despedida_s", 7)
QR_LADO         = _S.get("qr_lado_px", 300)

_P2 = _CFG.get("pinguino", {})
PING_ESCALA     = _P2.get("escala", 0.62)

# Cuántos cuadros seguidos hay que ver a alguien antes de reaccionar. Sin esto,
# un falso positivo suelto lanza al robot hacia un perchero.
CONFIRMAR_PERSONA = _CFG.get("acercamiento", {}).get("confirmar_frames", 3)

# ── MODO BANCO ───────────────────────────────────────────────────────────────
# Para probar la interacción completa con la Raspberry sola: pantalla, cámara,
# QR, celular y compra, SIN ESP32 ni ruedas.
#
#     ROBOT_SIN_MOTORES=1 python3 pi/pi_gui_gpio.py
#
# El único estado que necesita ruedas es ACERCAMIENTO: sin motores el robot no
# avanza, la persona nunca se ve más grande, y el flujo se quedaría trabado ahí
# hasta el timeout sin llegar nunca a mostrar el QR. En modo banco ese estado se
# saltea y se pasa directo a la invitación.
#
# Todo lo demás corre igual que en el robot de verdad, así que lo que valides acá
# vale después.
SIN_MOTORES = (os.environ.get("ROBOT_SIN_MOTORES", "").strip().lower()
               in ("1", "true", "si", "sí", "yes"))

_G = _CFG.get("gpio_pi", {})
PIN_BTN_OK    = _G.get("boton_ok",  13)
PIN_BTN_DEL   = _G.get("boton_del", 26)

# Lienzo VIRTUAL en el que se dibuja todo.
SCREEN_W, SCREEN_H = 1280, 720

# Panel FÍSICO (Tontec 3.5" SPI = 480x320). El lienzo virtual se escala a esto
# de una sola pasada al final de cada cuadro.
_P = _CFG.get("pantalla", {})
PANEL_W        = _P.get("ancho", SCREEN_W)
PANEL_H        = _P.get("alto",  SCREEN_H)
FULLSCREEN     = _P.get("fullscreen", True)
OCULTAR_CURSOR = _P.get("ocultar_cursor", True)
ESCALAR        = (PANEL_W, PANEL_H) != (SCREEN_W, SCREEN_H)

# A 480x320 sobre SPI el bus no da para 60 fps y solo calienta la CPU.
FPS = 30 if ESCALAR else 60

# ── Colores ─────────────────────────────────────────────────────────────────
class C:
    BG         = (10,  8, 28)
    BG2        = (20, 16, 50)
    CARD       = (28, 22, 65)
    CARD2      = (38, 30, 85)
    ORANGE     = (255, 95, 50)
    TEAL       = (60, 210, 190)
    GOLD       = (255, 210, 50)
    WHITE      = (255, 255, 255)
    GRAY       = (160, 155, 195)
    DIM        = (80,  75, 115)
    GREEN      = (50, 210, 130)
    RED        = (230, 70, 70)
    GLOW       = (120, 80, 255)

class State(Enum):
    IDLE          = auto()
    ACERCAMIENTO  = auto()
    INVITACION    = auto()
    ESPERA_CEDULA = auto()
    TELEOP        = auto()
    FACTURA       = auto()
    DESPEDIDA     = auto()

# ── Sonido ──────────────────────────────────────────────────────────────────
def _make_sound(freqs, durs, vol=0.5, sr=44100):
    waves = []
    for f, d in zip(freqs, durs):
        n = int(sr * d)
        t = np.linspace(0, d, n, endpoint=False)
        w = np.sin(2 * np.pi * f * t) * np.exp(-t * (5/d)) * vol
        waves.append(w)
    pcm = np.clip(np.concatenate(waves), -1, 1)
    s16 = (pcm * 32767).astype(np.int16)
    return pygame.sndarray.make_sound(np.column_stack([s16, s16]).copy())

class _SilentSound:
    """Sustituto mudo: deja que el robot funcione sin tarjeta de audio."""
    def play(self, *a, **kw): pass

def create_sounds():
    # Con la pantalla SPI no hay audio por HDMI; si el jack no está configurado,
    # mixer.init() lanza excepción. El robot debe seguir andando igual.
    try:
        pygame.mixer.init(44100, -16, 2, 512)
        return {
            "success": _make_sound([523,659,784,1047,1319],[0.12,0.12,0.12,0.18,0.35],0.6),
            "click":   _make_sound([880],[0.06],0.3),
            "scan":    _make_sound([1200,900],[0.07,0.1],0.35),
            "hola":    _make_sound([784,988,1175],[0.1,0.1,0.2],0.5),
            "error":   _make_sound([300,250],[0.1,0.2],0.4),
        }
    except Exception as exc:
        print(f"[GUI] Sin audio ({exc}) — el robot sigue, en silencio.")
        return {k: _SilentSound() for k in
                ("success","click","scan","hola","error")}

# ── Partículas confeti ──────────────────────────────────────────────────────
class Particle:
    COLS = [(255,95,50),(60,210,190),(255,210,50),(50,210,130),(120,80,255),(255,255,255)]
    def __init__(self): self.reset()
    def reset(self):
        self.x=random.randint(0,SCREEN_W); self.y=random.randint(-80,0)
        self.vy=random.uniform(2,6); self.vx=random.uniform(-2,2)
        self.col=random.choice(self.COLS)
        self.w=random.randint(6,14); self.h=random.randint(4,8)
        self.rot=random.uniform(0,360); self.dr=random.uniform(-4,4)
    def update(self):
        self.x+=self.vx; self.y+=self.vy; self.rot+=self.dr
        if self.y>SCREEN_H+20: self.reset()
    def draw(self,surf):
        s=pygame.Surface((self.w,self.h),pygame.SRCALPHA)
        s.fill((*self.col,200))
        r=pygame.transform.rotate(s,self.rot)
        surf.blit(r,r.get_rect(center=(int(self.x),int(self.y))))

# ── Helpers dibujo ──────────────────────────────────────────────────────────
def rrect(surf, col, rect, r=18, a=255):
    s=pygame.Surface((rect[2],rect[3]),pygame.SRCALPHA)
    pygame.draw.rect(s,(*col,a),(0,0,rect[2],rect[3]),border_radius=r)
    surf.blit(s,(rect[0],rect[1]))

def txt(surf, text, font, col, cx, cy, alpha=255):
    s=font.render(text,True,col)
    if alpha<255: s.set_alpha(alpha)
    r=s.get_rect(center=(cx,cy)); surf.blit(s,r); return r

def glow(surf, col, cx, cy, rad, w=3, a=70):
    for ri in range(rad, rad+15, 4):
        s=pygame.Surface((ri*2+4,ri*2+4),pygame.SRCALPHA)
        pygame.draw.circle(s,(*col,a),(ri+2,ri+2),ri,w)
        surf.blit(s,(cx-ri-2,cy-ri-2))


# ── APP ─────────────────────────────────────────────────────────────────────
class RobotApp:
    def __init__(self):
        pygame.init()
        flags = pygame.FULLSCREEN if FULLSCREEN else 0
        # display = lo que ve el panel físico;  screen = lienzo virtual 1280x720
        # donde dibuja todo el código. Se escala una vez por cuadro en run().
        self.display = pygame.display.set_mode((PANEL_W,PANEL_H),flags)
        # 32 bits explícitos: el framebuffer de un panel SPI suele ser RGB565
        # (16 bits) y smoothscale solo acepta superficies de 24/32.
        self.screen  = (pygame.Surface((SCREEN_W,SCREEN_H), depth=32)
                        if ESCALAR else self.display)
        self._panel_buf = (pygame.Surface((PANEL_W,PANEL_H), depth=32)
                           if ESCALAR else None)
        pygame.display.set_caption("Robot Pingüino")
        if OCULTAR_CURSOR:
            pygame.mouse.set_visible(False)
        print(f"[GUI] Panel {PANEL_W}x{PANEL_H}"
              + (f" (lienzo virtual {SCREEN_W}x{SCREEN_H} escalado)" if ESCALAR else ""))
        self.clock = pygame.time.Clock()

        def F(sz, bold=False):
            for n in ["Segoe UI","Ubuntu","DejaVu Sans","Arial","freesansbold"]:
                try: return pygame.font.SysFont(n,sz,bold=bold)
                except Exception: pass
            return pygame.font.Font(None,sz)

        self.F = {
            "xl":F(90,True),"lg":F(56,True),"md":F(38,True),
            "sm":F(26),"xs":F(18),"emoji":F(96),
        }
        self.sounds = create_sounds()

        self.state = State.IDLE
        self.state_ts = time.time()
        self.anim_t = 0.0

        self.particles = [Particle() for _ in range(80)]
        self.stars = [(random.randint(0,SCREEN_W),random.randint(0,SCREEN_H),
                       random.uniform(0.3,1.2)) for _ in range(120)]
        self.evt_q: Queue = Queue()

        # Personaje y accesorios
        self.ping = Pinguino()
        self.qrgen = GeneradorQR()

        self.vision = None
        self.uart = None
        self.ptz = None
        self._patrullando = False

        self.sesion = SesionClient(PC_IP, API_PORT)
        self.acerc = None
        self.qr_url = None
        self.pc_viva = False
        self.factura = None
        self.producto = None
        self._sonido_exito = False
        self._persona_seguidas = 0
        self._ultimo_mov = 0.0
        self._aviso = ""
        # Mientras corre una rutina (el baile, un giro), la cara no se cambia por
        # el joystick: el ESP32 sigue bailando varios segundos y quedaría el robot
        # haciendo una cosa y la pantalla mostrando otra. Se libera con el
        # EVT:RUTINA_FIN del firmware, y si no hay ESP32, por tiempo.
        self._animo_fijo_hasta = 0.0

        # Cola de comandos UART con ACK.
        #
        # send_command() espera la confirmación del ESP32 y reintenta: si el
        # cable está flojo son hasta 3 segundos bloqueado. Llamarlo desde el
        # bucle de dibujo congelaría la pantalla justo cuando hay alguien
        # mirándola. Van por un hilo aparte, en cola para que no se desordenen
        # (MODO tiene que llegar antes que el primer MOV).
        # Los MOV: NO pasan por acá: no esperan ACK y salen directo.
        self._uart_q: Queue = Queue()
        self._uart_worker = threading.Thread(target=self._loop_uart, daemon=True,
                                             name="UARTComandos")

        self._setup_gpio()
        self._start_modules()
        self._uart_worker.start()

    def _loop_uart(self):
        while True:
            metodo, args = self._uart_q.get()
            try:
                metodo(*args)
            except Exception as exc:
                print(f"[GUI] Comando UART falló: {exc}")

    def _cmd(self, nombre: str, *args):
        """Encola un comando del UART para que lo mande el hilo de fondo."""
        if not self.uart:
            return
        metodo = getattr(self.uart, nombre, None)
        if metodo:
            self._uart_q.put((metodo, args))

    # ── Arranque ─────────────────────────────────────────────────────────────

    def _setup_gpio(self):
        """
        Los pulsadores quedan para el operador, no para el cliente: ya no se
        ingresa la cédula con ellos. [OK] saltea el estado (sirve en demos) y
        [DEL] aborta la interacción y devuelve el robot a su ruta.

        Todo va dentro de un try: en la Raspberry Pi 5 el GPIO pasó por el chip
        RP1 y, según la combinación de kernel y versión de gpiozero, la fábrica
        de pines puede no encontrar el gpiochip correcto (busca el 4 cuando en
        los kernels nuevos es el 0) y lanzar excepción al crear el Button.
        Sería absurdo que el robot no arrancara por eso: los botones son una
        comodidad del operador, no una pieza del flujo con el cliente.
        """
        self._btns = {}
        if not GPIO_AVAILABLE:
            print("[GUI] Sin gpiozero. Teclado: Enter = saltear | Backspace = cancelar")
            return
        try:
            for nombre, pin in (("BTN_OK", PIN_BTN_OK), ("BTN_DEL", PIN_BTN_DEL)):
                b = Button(pin, pull_up=True, bounce_time=0.05)
                b.when_pressed = lambda n=nombre: self.evt_q.put(n)
                self._btns[nombre] = b
            print(f"[GUI] GPIO listo (operador): OK={PIN_BTN_OK} DEL={PIN_BTN_DEL}")
        except Exception as exc:
            self._btns = {}
            print(f"[GUI] Sin botones GPIO ({exc}).")
            print("[GUI] En la Pi 5 suele arreglarse con: "
                  "sudo apt install python3-lgpio  y  "
                  "export GPIOZERO_PIN_FACTORY=lgpio")
            print("[GUI] Mientras tanto: Enter = saltear estado | Backspace = cancelar")

    def _map_key(self, k):
        return {pygame.K_RETURN: "BTN_OK",
                pygame.K_BACKSPACE: "BTN_DEL"}.get(k)

    def _start_modules(self):
        if VISION_OK:
            try:
                self.vision = VisionClient(pc_ip=PC_IP); self.vision.start()
            except Exception as e:
                print(f"[GUI] Vision: {e}")
        if UART_OK:
            try:
                self.uart = UARTController()
                if self.uart.connect():
                    self.uart.on_evento(self._evento_esp32)
                else:
                    self.uart = None
            except Exception as e:
                print(f"[GUI] UART: {e}")
                self.uart = None

        if self.uart is None and not SIN_MOTORES:
            print("[GUI] Sin ESP32. Para probar el flujo completo sin ruedas: "
                  "ROBOT_SIN_MOTORES=1 python3 pi/pi_gui_gpio.py")

        self.acerc = Acercamiento(self.uart)

        if PTZ_OK:
            try:
                self.ptz = FoscamPTZ(); self.ptz.start()
                self._set_patrulla(True)
            except Exception as e:
                print(f"[GUI] PTZ: {e}")

        # ¿Está viva la PC? Determina si el QR va a servir para algo.
        self.pc_viva = self.sesion.verificar_pc()
        print(f"[GUI] PC en {API_BASE}: {'OK' if self.pc_viva else 'NO RESPONDE'}")

        self._entrar_idle()

    def _evento_esp32(self, evento: str):
        """Llega desde el hilo lector del UART."""
        if evento == "DEADMAN":
            self._aviso = "Se perdió el control — robot detenido"
        elif evento == "RUTINA_FIN":
            # El robot terminó de bailar: la cara puede volver a responder al
            # joystick. Con el ESP32 conectado esto es exacto; sin él manda el
            # tope por tiempo de _ejecutar_accion.
            self._animo_fijo_hasta = 0.0

    def _set_patrulla(self, encender: bool):
        """Enciende/apaga el barrido de la cámara, sin repetir la orden."""
        if not self.ptz:
            return
        if encender != self._patrullando:
            self.ptz.patrulla(encender)
            self._patrullando = encender

    # ── Máquina de estados ───────────────────────────────────────────────────

    def _goto(self, st):
        self.state = st
        self.state_ts = time.time()
        self._aviso = ""
        print(f"[GUI] → {st.name}")

        if st == State.IDLE:
            self._entrar_idle()
        elif st == State.ACERCAMIENTO:
            self._set_patrulla(False)
            self.ping.set_animo(Pinguino.SORPRENDIDO)
            self.sounds["hola"].play()
            if self.vision: self.vision.set_mode("PERSONA")
            self.acerc.iniciar()
        elif st == State.INVITACION:
            self._entrar_invitacion()
        elif st == State.ESPERA_CEDULA:
            self.ping.set_animo(Pinguino.HABLANDO)
            self.sounds["scan"].play()
        elif st == State.TELEOP:
            self.ping.set_animo(Pinguino.FELIZ)
            self.sounds["success"].play()
            self._cmd("modo", "TELEOP")
        elif st == State.FACTURA:
            self.ping.set_animo(Pinguino.HABLANDO)
            self.sounds["success"].play()
            self._cmd("modo", "PAUSA")
        elif st == State.DESPEDIDA:
            self._entrar_despedida()

    def _entrar_idle(self):
        """Reposo: el robot vuelve a su ruta autónoma y el pingüino se duerme."""
        self.ping.set_animo(Pinguino.DORMIDO)
        self.ping.mirar_al_frente()
        self.qr_url = None
        self.factura = None
        self.producto = None
        self._sonido_exito = False
        self._persona_seguidas = 0
        self.sesion.cerrar(cancelar=True)
        self._cmd("modo", "AUTO")
        self._cmd("rodar", RODAR_VELOCIDAD)
        if self.vision:
            self.vision.set_mode("PERSONA")
        self._set_patrulla(True)

    def _entrar_invitacion(self):
        """Ya está enfrente de la persona: se congela y se muestra el QR."""
        self.ping.set_animo(Pinguino.FELIZ)
        self.ping.mirar_al_frente()
        self._cmd("modo", "PAUSA")
        self._cmd("rutina", "SALUDO")
        if self.vision:
            self.vision.set_mode("PERSONA")

        # El QR solo tiene sentido si la PC responde. Si no, se avisa en pantalla
        # en vez de mostrar un código que lleva a ninguna parte.
        self.pc_viva = self.sesion.verificar_pc()
        if not self.pc_viva:
            self.qr_url = None
            self._aviso = "Sin conexión con el servidor"
            print("[GUI] La PC no responde — no se puede abrir sesión.")
            return

        datos = self.sesion.abrir_sesion()
        if datos:
            _, self.qr_url = datos
            self.sesion.start()
            self.sounds["scan"].play()
        else:
            self.qr_url = None
            self._aviso = "No se pudo abrir la sesión"

    def _entrar_despedida(self):
        """Baila, se despide, gira y retoma la ruta."""
        self.ping.set_animo(Pinguino.BAILANDO)
        if not self._sonido_exito:
            self.sounds["success"].play()
            self._sonido_exito = True
        self._cmd("modo", "AUTO")
        self._cmd("rutina", "BAILE")

    def _elapsed(self):
        return time.time() - self.state_ts

    # ── Eventos de los pulsadores (operador) ─────────────────────────────────

    def _handle_evt(self, ev):
        if ev == "BTN_DEL":
            # Abortar: se corta todo y el robot vuelve a lo suyo.
            print("[GUI] Cancelado por el operador.")
            self._cmd("parar")
            self._goto(State.DESPEDIDA)
        elif ev == "BTN_OK":
            # Saltear el estado actual — para demostraciones.
            salto = {
                State.IDLE:          State.ACERCAMIENTO,
                State.ACERCAMIENTO:  State.INVITACION,
                State.INVITACION:    State.ESPERA_CEDULA,
                State.ESPERA_CEDULA: State.TELEOP,
                State.TELEOP:        State.FACTURA,
                State.FACTURA:       State.DESPEDIDA,
                State.DESPEDIDA:     State.IDLE,
            }.get(self.state)
            if salto:
                self.sounds["click"].play()
                self._goto(salto)

    # ── Sondeo de la visión ──────────────────────────────────────────────────

    def _poll_vision(self):
        r = self.vision.get_last_result() if self.vision else None

        # El pingüino sigue con la mirada a quien tenga delante. Es el detalle
        # que hace que la gente se dé cuenta de que el robot la está viendo.
        if r:
            px, hx = r.get("persona_x"), r.get("hand_x")
            if px is not None:
                self.ping.mirar(px, r.get("persona_y") or 0.5)
            elif hx is not None:
                self.ping.mirar(hx, r.get("hand_y") or 0.5)

        if self.state == State.IDLE:
            if not r:
                return
            # Se acepta el disparo por persona o, como antes, por gesto de mano:
            # si el detector de personas no cargó, el robot sigue siendo usable.
            if r.get("persona_detectada"):
                self._persona_seguidas += 1
            elif r.get("hand_detected") or r.get("thumbs_up"):
                self._persona_seguidas = CONFIRMAR_PERSONA
            else:
                self._persona_seguidas = 0

            if self._persona_seguidas >= CONFIRMAR_PERSONA:
                if self.ptz and r.get("persona_x") is not None:
                    self.ptz.seguir(r.get("persona_x"), r.get("persona_y") or 0.5)
                # Sin ruedas no tiene sentido pasar por el acercamiento: el robot
                # no se puede mover, así que se saluda desde donde está.
                self._goto(State.INVITACION if SIN_MOTORES
                           else State.ACERCAMIENTO)

        elif self.state == State.ACERCAMIENTO:
            estado = self.acerc.actualizar(r)
            if estado == Acercamiento.LLEGO:
                self._goto(State.INVITACION)
            elif estado in (Acercamiento.PERDIDA, Acercamiento.TIMEOUT):
                self._goto(State.IDLE)
            elif estado == Acercamiento.AVANZANDO:
                self.ping.set_animo(Pinguino.ATENTO)

    # ── Sondeo de la sesión (el celular del cliente) ─────────────────────────

    def _poll_sesion(self):
        if self.state not in (State.INVITACION, State.ESPERA_CEDULA, State.TELEOP):
            return

        fase = self.sesion.fase()

        if self.state == State.INVITACION and fase == "conectado":
            self._goto(State.ESPERA_CEDULA)
        elif self.state in (State.INVITACION, State.ESPERA_CEDULA) and fase == "validada":
            self._goto(State.TELEOP)
        elif self.state == State.TELEOP and fase == "finalizada":
            self._cargar_factura()
            self._goto(State.FACTURA)
        elif fase in ("expirada", "cancelada") and self.state != State.INVITACION:
            self._goto(State.DESPEDIDA)

        if self.state == State.TELEOP:
            self._conducir()

    def _conducir(self):
        """Traslada el joystick del celular a las ruedas, y las acciones sueltas."""
        ctrl = self.sesion.control()
        v, w = ctrl if ctrl else (0.0, 0.0)

        # Se manda a ~20 Hz. Nunca `parar()` acá: PARAR saca al firmware del
        # estado de teleoperación y el joystick dejaría de responder. Un MOV de
        # ceros frena igual y mantiene vivo el lazo.
        #
        # Sin ESP32 esto no se manda, pero el resto sigue corriendo: así en modo
        # banco se ve al pingüino reaccionar y las barras moverse en pantalla
        # mientras alguien maneja desde el celular.
        ahora = time.time()
        if self.uart and ahora - self._ultimo_mov >= 0.05:
            self.uart.mov(v, w)
            self._ultimo_mov = ahora

        if (abs(v) > 0.05 or abs(w) > 0.05) and time.time() > self._animo_fijo_hasta:
            self.ping.set_animo(Pinguino.ATENTO)

        for a in self.sesion.tomar_acciones():
            self._ejecutar_accion(a.get("accion", ""))

    def _ejecutar_accion(self, accion: str):
        print(f"[GUI] Acción del cliente: {accion}")
        self.sounds["click"].play()
        if accion == "BAILE":
            self.ping.set_animo(Pinguino.BAILANDO)
            # Tope de seguridad: el baile del firmware dura ~6 s. Si hay ESP32,
            # el EVT:RUTINA_FIN lo corta antes y con precisión.
            self._animo_fijo_hasta = time.time() + 7.0
            self._cmd("rutina", "BAILE")
        elif accion == "GIRO_IZQ":
            self._cmd("giro", -90)
        elif accion == "GIRO_DER":
            self._cmd("giro", 90)
        elif accion == "PIVOTE_IZQ":
            self._cmd("pivote", "I", 90, 0.35)
        elif accion == "PIVOTE_DER":
            self._cmd("pivote", "D", 90, 0.35)
        elif accion == "SALUDO":
            self.ping.set_animo(Pinguino.FELIZ)
            self._animo_fijo_hasta = time.time() + 2.5
            self._cmd("rutina", "SALUDO")

    def _cargar_factura(self):
        """Trae de la PC los datos de la venta recién cerrada, para el comprobante."""
        def _worker():
            try:
                url = f"{API_BASE}/api/transacciones?cedula={self.sesion.cedula()}&limit=1"
                with urllib.request.urlopen(url, timeout=3) as resp:
                    datos = json.loads(resp.read())
                if datos:
                    self.factura = datos[0]
                pid = self.sesion.producto_id()
                if pid:
                    with urllib.request.urlopen(
                            f"{API_BASE}/api/productos/{pid}", timeout=3) as resp:
                        self.producto = json.loads(resp.read())
            except Exception as exc:
                print(f"[GUI] No se pudo traer la factura: {exc}")
        threading.Thread(target=_worker, daemon=True).start()

    # ── PANTALLAS ────────────────────────────────────────────────────────────

    def _bg(self):
        self.screen.fill(C.BG)
        t = time.time()
        for sx, sy, sp in self.stars:
            b = int(100 + 80*math.sin(t*sp + sx))
            pygame.draw.circle(self.screen, (b,b,b),
                               (sx, int((sy + t*sp*8) % SCREEN_H)), 1)

    def _pie(self, texto, color=C.DIM):
        rrect(self.screen, C.CARD, (0, SCREEN_H-46, SCREEN_W, 46), r=0)
        txt(self.screen, texto, self.F["xs"], color, SCREEN_W//2, SCREEN_H-23)

    def _pinguino(self, cx, cy, escala=None):
        self.ping.dibujar(self.screen, cx, cy, escala or PING_ESCALA)

    def _draw_IDLE(self):
        self._bg()
        t = self.anim_t
        glow(self.screen, C.GLOW, SCREEN_W//2, SCREEN_H//2-30, 170, 2, 28)
        self._pinguino(SCREEN_W//2, SCREEN_H//2-40)
        a = int(140 + 100*math.sin(t*1.5))
        txt(self.screen, "Pingüi está descansando…", self.F["md"], C.GRAY,
            SCREEN_W//2, SCREEN_H-110, a)
        if SIN_MOTORES:
            estado = "● modo banco (sin ruedas)"
        elif self.uart and self.uart.is_connected:
            estado = "● en ruta"
        else:
            estado = "● sin ESP32"
        self._pie(f"{estado}   ·   PC {'OK' if self.pc_viva else 'sin conexión'}",
                  C.DIM if self.pc_viva else C.RED)

    def _draw_ACERCAMIENTO(self):
        self._bg()
        self._pinguino(SCREEN_W//2, SCREEN_H//2-50)
        txt(self.screen, "¡Hola! Ya voy…", self.F["lg"], C.TEAL,
            SCREEN_W//2, SCREEN_H-140)
        a = int(150 + 90*math.sin(self.anim_t*3))
        txt(self.screen, "Quedate ahí un segundito", self.F["sm"], C.GRAY,
            SCREEN_W//2, SCREEN_H-85, a)
        self._pie(f"acercándome — {self.acerc.estado}")

    def _draw_INVITACION(self):
        self._bg()
        # Pingüino a la izquierda, QR a la derecha.
        self._pinguino(300, SCREEN_H//2-20, PING_ESCALA*0.85)

        qx, qy = 880, SCREEN_H//2 - 40
        if self.qr_url:
            self.qrgen.dibujar(self.screen, self.qr_url, (qx, qy),
                               QR_LADO, self.F["xs"])
            txt(self.screen, "Escaneá el código con tu celular", self.F["md"],
                C.WHITE, qx, qy + QR_LADO//2 + 52)
            a = int(160 + 90*math.sin(self.anim_t*2))
            txt(self.screen, self.qr_url.replace("http://", ""), self.F["xs"],
                C.DIM, qx, qy + QR_LADO//2 + 92, a)
        else:
            rrect(self.screen, C.CARD, (qx-QR_LADO//2, qy-QR_LADO//2,
                                        QR_LADO, QR_LADO), r=20)
            txt(self.screen, "⚠", self.F["xl"], C.RED, qx, qy-30)
            txt(self.screen, self._aviso or "Sin sesión", self.F["sm"],
                C.RED, qx, qy+60)

        txt(self.screen, "¡Hola!", self.F["lg"], C.GOLD, 300, SCREEN_H-150)
        restante = max(0, int(T_INVITACION - self._elapsed()))
        self._pie(f"esperando a que escanees… {restante}s")

    def _draw_ESPERA_CEDULA(self):
        self._bg()
        self._pinguino(300, SCREEN_H//2-20, PING_ESCALA*0.85)
        cx = 830
        rrect(self.screen, C.CARD, (cx-330, 150, 660, 380), r=24)
        rrect(self.screen, C.TEAL, (cx-330, 150, 660, 8), r=0)
        txt(self.screen, "📱", self.F["emoji"], C.WHITE, cx, 250)
        txt(self.screen, "¡Conectado!", self.F["lg"], C.TEAL, cx, 355)
        txt(self.screen, "Ingresá tu cédula", self.F["md"], C.WHITE, cx, 425)
        txt(self.screen, "en tu teléfono", self.F["sm"], C.GRAY, cx, 475)
        restante = max(0, int(T_CEDULA - self._elapsed()))
        self._pie(f"esperando la cédula… {restante}s")

    def _draw_TELEOP(self):
        self._bg()
        ctrl = self.sesion.control()
        v, w = ctrl if ctrl else (0.0, 0.0)

        self._pinguino(320, SCREEN_H//2-30, PING_ESCALA*0.9)

        cx = 850
        rrect(self.screen, C.CARD, (cx-330, 90, 660, 300), r=24)
        rrect(self.screen, C.ORANGE, (cx-330, 90, 660, 8), r=0)
        txt(self.screen, "¡Manejame!", self.F["lg"], C.GOLD, cx, 155)
        ced = self.sesion.cedula() or ""
        if ced:
            txt(self.screen, f"Cliente {ced}", self.F["sm"], C.GRAY, cx, 205)

        # Barras de avance y giro: retroalimentación para el que maneja.
        self._barra(cx-260, 250, 520, 26, v, "avance", C.TEAL)
        self._barra(cx-260, 310, 520, 26, w, "giro",   C.ORANGE)

        if self._aviso:
            txt(self.screen, self._aviso, self.F["sm"], C.RED, cx, 420)
        elif not ctrl:
            a = int(120 + 100*math.sin(self.anim_t*2.5))
            txt(self.screen, "esperando tus órdenes…", self.F["sm"], C.DIM,
                cx, 420, a)

        restante = max(0, int(T_TELEOP - self._elapsed()))
        self._pie(f"el cliente tiene el control   ·   {restante}s")

    def _barra(self, x, y, ancho, alto, valor, etiqueta, color):
        """Barra bipolar de -1 a 1 con el cero en el centro."""
        rrect(self.screen, C.CARD2, (x, y, ancho, alto), r=alto//2)
        medio = x + ancho//2
        pygame.draw.line(self.screen, C.DIM, (medio, y), (medio, y+alto), 2)
        largo = int((ancho//2 - 4) * max(-1.0, min(1.0, valor)))
        if largo >= 0:
            rrect(self.screen, color, (medio, y+3, max(2,largo), alto-6), r=(alto-6)//2)
        else:
            rrect(self.screen, color, (medio+largo, y+3, max(2,-largo), alto-6),
                  r=(alto-6)//2)
        txt(self.screen, f"{etiqueta}  {valor:+.2f}", self.F["xs"], C.GRAY,
            x + ancho//2, y - 16)

    def _draw_FACTURA(self):
        self._bg()
        self._pinguino(250, SCREEN_H//2-30, PING_ESCALA*0.75)
        cx, cy, cw, ch = 560, 70, 660, 540
        rrect(self.screen, C.CARD, (cx, cy, cw, ch), r=20)
        rrect(self.screen, C.GREEN, (cx, cy, cw, 6), r=0)
        txt(self.screen, "COMPROBANTE", self.F["md"], C.GREEN, cx+cw//2, cy+50)
        for dx in range(0, cw-36, 16):
            pygame.draw.rect(self.screen, C.DIM, (cx+18+dx, cy+84, 8, 2))

        filas = [("Cédula", self.sesion.cedula() or "—")]
        if self.producto:
            p = self.producto
            filas += [("Producto", p["nombre"]),
                      ("Precio base", f"${p['precio_base']:.2f}"),
                      ("Descuento", f"{p['descuento_pct']}%")]
        if self.factura and self.factura.get("id"):
            filas.append(("Comprobante", f"N.º {self.factura['id']}"))

        yr = cy + 118
        for etiqueta, valor in filas:
            txt(self.screen, etiqueta, self.F["sm"], C.GRAY, cx+150, yr)
            txt(self.screen, str(valor), self.F["sm"], C.WHITE, cx+cw-150, yr)
            yr += 48
        for dx in range(0, cw-36, 16):
            pygame.draw.rect(self.screen, C.DIM, (cx+18+dx, yr+4, 8, 2))

        total = None
        if self.factura and self.factura.get("precio_final") is not None:
            total = self.factura["precio_final"]
        elif self.producto:
            total = self.producto["precio_final"]
        if total is not None:
            txt(self.screen, "TOTAL", self.F["md"], C.GOLD, cx+150, yr+52)
            txt(self.screen, f"${total:.2f}", self.F["md"], C.GOLD,
                cx+cw-150, yr+52)

        self._pie("venta registrada")

    def _draw_DESPEDIDA(self):
        self._bg()
        for p in self.particles:
            p.update(); p.draw(self.screen)
        self._pinguino(SCREEN_W//2, SCREEN_H//2-50)
        txt(self.screen, "¡Muchas gracias!", self.F["lg"], C.GOLD,
            SCREEN_W//2, SCREEN_H-150)
        txt(self.screen, "Nos vemos pronto", self.F["md"], C.TEAL,
            SCREEN_W//2, SCREEN_H-90)

    # ── LOOP ─────────────────────────────────────────────────────────────────

    def run(self):
        dibujantes = {
            State.IDLE:          self._draw_IDLE,
            State.ACERCAMIENTO:  self._draw_ACERCAMIENTO,
            State.INVITACION:    self._draw_INVITACION,
            State.ESPERA_CEDULA: self._draw_ESPERA_CEDULA,
            State.TELEOP:        self._draw_TELEOP,
            State.FACTURA:       self._draw_FACTURA,
            State.DESPEDIDA:     self._draw_DESPEDIDA,
        }
        try:
            while True:
                dt = self.clock.tick(FPS) / 1000.0
                self.anim_t += dt

                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            return
                        m = self._map_key(event.key)
                        if m:
                            self.evt_q.put(m)

                try:
                    while True:
                        self._handle_evt(self.evt_q.get_nowait())
                except Empty:
                    pass

                self._poll_vision()
                self._poll_sesion()
                self._timeouts()

                self.ping.actualizar(dt)
                dibujantes[self.state]()

                if ESCALAR:
                    # smoothscale mantiene legible el texto al bajar a 480x320;
                    # scale a secas deja los bordes de las letras dentados.
                    pygame.transform.smoothscale(self.screen,
                                                 (PANEL_W, PANEL_H),
                                                 self._panel_buf)
                    self.display.blit(self._panel_buf, (0, 0))
                pygame.display.flip()
        finally:
            self.cerrar()

    def _timeouts(self):
        """
        Ningún estado puede quedarse colgado. Un robot congelado esperando a un
        cliente que ya se fue es peor que uno que se equivoca: deja de trabajar.
        """
        el = self._elapsed()
        s = self.state
        if   s == State.INVITACION    and el > T_INVITACION: self._goto(State.DESPEDIDA)
        elif s == State.ESPERA_CEDULA and el > T_CEDULA:     self._goto(State.DESPEDIDA)
        elif s == State.TELEOP        and el > T_TELEOP:     self._goto(State.DESPEDIDA)
        elif s == State.FACTURA       and el > T_FACTURA:    self._goto(State.DESPEDIDA)
        elif s == State.DESPEDIDA     and el > T_DESPEDIDA:
            # Se despidió: gira y retoma la ruta original.
            self._cmd("giro", GIRO_FINAL)
            self._goto(State.IDLE)

    def cerrar(self):
        print("[GUI] Cerrando…")
        try:
            self.sesion.cerrar(cancelar=True)
        except Exception:
            pass
        if self.uart:
            try:
                self.uart.parar()
                self.uart.disconnect()
            except Exception:
                pass
        if self.vision:
            try:
                self.vision.stop()
            except Exception:
                pass
        pygame.quit()


if __name__ == "__main__":
    print("=" * 62)
    print("  ROBOT PINGÜINO — interfaz de la Raspberry Pi")
    print(f"  PC: {API_BASE}   (ROBOT_PC_IP la pisa)")
    print("=" * 62)
    RobotApp().run()
