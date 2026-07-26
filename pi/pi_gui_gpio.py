#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT BÍPEDO — GUI Principal (Pygame) + GPIO Joystick + Botonera
=============================================================================
Entorno virtual : /home/pi/mi_proyecto_env
Activar         : source ~/mi_proyecto_env/bin/activate

Dependencias adicionales:
    pip install pygame numpy

Hardware — 4 botones en protoboard (pull-up interno de la Pi):
  ┌────────────────┬──────┬───────────┬──────────────────────────┐
  │ Botón          │ GPIO │ Pin físico│ Función                  │
  ├────────────────┼──────┼───────────┼──────────────────────────┤
  │ B1  [+]        │  17  │  Pin 11   │ Incrementa dígito (0→9)  │
  │ B2  [-]        │  27  │  Pin 13   │ Decrementa dígito (9→0)  │
  │ B3  [OK/→]     │  22  │  Pin 15   │ Confirma y avanza        │
  │ B4  [DEL/←]   │  23  │  Pin 16   │ Borra y retrocede        │
  └────────────────┴──────┴───────────┴──────────────────────────┘
  Un extremo del pulsador → GPIO, el otro extremo → GND.
  Pull-up interno activado (no necesita resistencia externa).

Flujo de estados:
  IDLE → (👍 pulgar arriba) → QR_SCAN
  QR_SCAN → (QR detectado) → SALUDO
  SALUDO → (3 s / OK) → CEDULA
  CEDULA → (10 dígitos) → FACTURA
  FACTURA → (OK / 12 s) → EXITO
  EXITO → (6 s) → IDLE
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
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
try:
    from pi_zmq_client import VisionClient
    from pi_uart import UARTController
    MODULES_OK = True
except ImportError:
    MODULES_OK = False

# ── Config ─────────────────────────────────────────────────────────────────
_CFG_PATH = _HERE.parent / "config.json"
try:
    _CFG = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
except Exception:
    _CFG = {}

PC_IP         = _CFG.get("red", {}).get("pc_ip", "192.168.1.100")
API_PORT      = _CFG.get("red", {}).get("api_puerto", 8000)
API_BASE      = f"http://{PC_IP}:{API_PORT}"
CEDULA_DIGITS = _CFG.get("robot", {}).get("cedula_digitos", 10)
# Avance al detectar la mano (lo ejecuta el ESP32 con AVANZAR_T)
AVANCE_MS       = _CFG.get("robot", {}).get("avance_al_ver_mano_ms", 5000)
AVANCE_VELOCIDAD = _CFG.get("robot", {}).get("avance_al_ver_mano_velocidad", 0.25)
# Modo atracción: ruedas rodando continuo, giro contrario al ver la mano, timeout de QR
RODAR_VELOCIDAD  = _CFG.get("robot", {}).get("rodar_velocidad", 0.10)
GIRO_CONTRARIO_MS  = _CFG.get("robot", {}).get("giro_contrario_ms", 1000)
GIRO_CONTRARIO_VEL = _CFG.get("robot", {}).get("giro_contrario_velocidad", -0.10)
QR_TIMEOUT_S     = _CFG.get("robot", {}).get("qr_timeout_s", 20)

_G = _CFG.get("gpio_pi", {})
# 4 botones físicos en protoboard (pull-up interno de la Pi)
#   B1 [+]     → GPIO 17  — incrementa dígito
#   B2 [-]     → GPIO 27  — decrementa dígito
#   B3 [OK]    → GPIO 22  — confirma dígito / avanza
#   B4 [DEL]   → GPIO 23  — borra último / retrocede
PIN_BTN_PLUS  = _G.get("boton_mas",   17)
PIN_BTN_MINUS = _G.get("boton_menos", 27)
PIN_BTN_OK    = _G.get("boton_ok",    22)
PIN_BTN_DEL   = _G.get("boton_del",   23)

SCREEN_W, SCREEN_H = 1280, 720
FPS = 60

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
    DGIT_IDLE  = (35,  28,  80)
    DGIT_ACT   = (80,  60, 170)
    DGIT_DONE  = (25,  80,  70)
    GLOW       = (120, 80, 255)

class State(Enum):
    IDLE    = auto()
    QR_SCAN = auto()
    SALUDO  = auto()
    CEDULA  = auto()
    FACTURA = auto()
    EXITO   = auto()

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

def create_sounds():
    pygame.mixer.init(44100, -16, 2, 512)
    return {
        "success": _make_sound([523,659,784,1047,1319],[0.12,0.12,0.12,0.18,0.35],0.6),
        "click":   _make_sound([880],[0.06],0.3),
        "scan":    _make_sound([1200,900],[0.07,0.1],0.35),
        "error":   _make_sound([300,250],[0.1,0.2],0.4),
    }

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
        flags = pygame.FULLSCREEN if GPIO_AVAILABLE else 0
        self.screen = pygame.display.set_mode((SCREEN_W,SCREEN_H),flags)
        pygame.display.set_caption("RoboMart")
        self.clock = pygame.time.Clock()

        def F(sz, bold=False):
            for n in ["Segoe UI","Ubuntu","DejaVu Sans","Arial","freesansbold"]:
                try: return pygame.font.SysFont(n,sz,bold=bold)
                except: pass
            return pygame.font.Font(None,sz)

        self.F = {
            "xl":F(90,True),"lg":F(56,True),"md":F(38,True),
            "sm":F(26),"xs":F(18),"digit":F(50,True),"emoji":F(96),
        }
        self.sounds = create_sounds()
        self.state = State.IDLE
        self.state_ts = time.time()
        self.anim_t = 0.0
        self.digits = [0]*CEDULA_DIGITS
        self.cur_pos = 0
        self.qr_info = None
        self.qr_raw  = None          # Código QR crudo, para registrar la venta
        self.cedula_str = ""
        self.particles = [Particle() for _ in range(80)]
        self.stars = [(random.randint(0,SCREEN_W),random.randint(0,SCREEN_H),random.uniform(0.3,1.2)) for _ in range(120)]
        self.evt_q: Queue = Queue()
        self._sound_exito_played = False
        self.vision = None
        self.uart = None
        self._setup_gpio()
        self._start_modules()

    def _setup_gpio(self):
        if GPIO_AVAILABLE:
            pins = {
                "JOY_UP":   PIN_BTN_PLUS,
                "JOY_DOWN": PIN_BTN_MINUS,
                "BTN_OK":   PIN_BTN_OK,
                "BTN_BACK": PIN_BTN_DEL,
            }
            self._btns = {}
            for name, pin in pins.items():
                b = Button(pin, pull_up=True, bounce_time=0.05)
                b.when_pressed = lambda n=name: self.evt_q.put(n)
                self._btns[name] = b
            print(f"[GUI] GPIO listo: +={PIN_BTN_PLUS} -={PIN_BTN_MINUS} "
                  f"OK={PIN_BTN_OK} DEL={PIN_BTN_DEL}")
        else:
            print("[GUI] Teclado: ↑↓ dígito | ←→ cursor | Enter=OK | Backspace=DEL")

    def _map_key(self, k):
        # ↑/W = B1[+]  |  ↓/S = B2[-]  |  Enter = B3[OK]  |  Backspace = B4[DEL]
        return {
            pygame.K_UP:        "JOY_UP",
            pygame.K_w:         "JOY_UP",
            pygame.K_DOWN:      "JOY_DOWN",
            pygame.K_s:         "JOY_DOWN",
            pygame.K_RETURN:    "BTN_OK",
            pygame.K_BACKSPACE: "BTN_BACK",
        }.get(k)

    def _start_modules(self):
        if not MODULES_OK: return
        try:
            self.vision=VisionClient(pc_ip=PC_IP); self.vision.start()
        except Exception as e: print(f"[GUI] Vision: {e}")
        try:
            self.uart=UARTController(); self.uart.connect()
            # Arranca en modo atracción: ruedas rodando continuo
            if self.uart: self.uart.send_command(f"RODAR:{RODAR_VELOCIDAD}")
        except Exception as e: print(f"[GUI] UART: {e}")

    def _goto(self, st):
        self.state=st; self.state_ts=time.time(); self.anim_t=0.0
        if st==State.CEDULA: self.digits=[0]*CEDULA_DIGITS; self.cur_pos=0
        if st==State.EXITO: self._sound_exito_played=False
        # Al volver a reposo (atracción): rueda continuo y la cámara vuelve a
        # detectar la mano (por si venía de QR_SCAN, p.ej. tras timeout).
        if st==State.IDLE:
            if self.uart: self.uart.send_command(f"RODAR:{RODAR_VELOCIDAD}")
            if self.vision: self.vision.set_mode("HAND")
        print(f"[GUI] → {st.name}")

    def _elapsed(self): return time.time()-self.state_ts

    def _handle_evt(self, ev):
        s = self.state
        if s == State.CEDULA:
            if ev == "JOY_UP":
                # B1 [+]: incrementa dígito actual
                self.digits[self.cur_pos] = (self.digits[self.cur_pos] + 1) % 10
                self.sounds["click"].play()
            elif ev == "JOY_DOWN":
                # B2 [-]: decrementa dígito actual
                self.digits[self.cur_pos] = (self.digits[self.cur_pos] - 1) % 10
                self.sounds["click"].play()
            elif ev == "BTN_OK":
                # B3 [OK]: confirma y avanza (o envía si es el último)
                if self.cur_pos < CEDULA_DIGITS - 1:
                    self.cur_pos += 1
                    self.sounds["click"].play()
                else:
                    self.cedula_str = "".join(str(d) for d in self.digits)
                    # Cédula + QR listos: registrar la venta en la PC (SQLite + Firebase)
                    self._registrar_venta(self.cedula_str, self.qr_raw)
                    if self.vision: self.vision.set_mode("HAND")
                    self._goto(State.FACTURA)
            elif ev == "BTN_BACK":
                # B4 [DEL]: borra dígito actual y retrocede
                if self.cur_pos > 0:
                    self.cur_pos -= 1
                    self.digits[self.cur_pos] = 0
                    self.sounds["click"].play()
        elif s == State.SALUDO:
            if ev == "BTN_OK" and self._elapsed() > 1:
                self._goto(State.CEDULA)
        elif s == State.FACTURA:
            if ev == "BTN_OK" and self._elapsed() > 1.5:
                self._goto(State.EXITO)

    def _poll_vision(self):
        if not self.vision: return
        r=self.vision.get_last_result()
        if not r: return
        if self.state==State.IDLE:
            if r.get("hand_detected") or r.get("thumbs_up"):
                # Al ver la mano: giro contrario 1s y frena (giro en la maqueta),
                # luego pasa a esperar el QR. Lo ejecuta el ESP32 (AVANZAR_T con
                # duty negativo = sentido contrario al rodado).
                self.sounds["scan"].play()
                if self.uart:
                    self.uart.send_command(
                        f"AVANZAR_T:{int(GIRO_CONTRARIO_MS)}:{GIRO_CONTRARIO_VEL}")
                if self.vision: self.vision.set_mode("QR")
                self._goto(State.QR_SCAN)
        elif self.state==State.QR_SCAN:
            qr=r.get("qr_data")
            if qr:
                self.sounds["scan"].play()
                self.qr_raw=qr
                self.qr_info=self._parse_qr(qr)
                self._goto(State.SALUDO)

    def _registrar_venta(self, cedula, qr_codigo):
        """
        Envía la venta a la API de la PC (POST /api/transacciones) en un hilo
        aparte para no congelar la GUI. La PC la guarda en SQLite y la sube a
        Firebase. Si la PC no responde, el robot sigue funcionando igual.
        """
        if not cedula or not qr_codigo:
            print("[VENTA] Sin cédula o QR — no se registra.")
            return

        def _worker():
            try:
                datos = json.dumps({"cedula": cedula,
                                    "qr_codigo": qr_codigo}).encode("utf-8")
                req = urllib.request.Request(
                    f"{API_BASE}/api/transacciones",
                    data=datos,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    print(f"[VENTA] Registrada en la PC (HTTP {resp.status}).")
            except Exception as exc:
                print(f"[VENTA] No se pudo registrar en la PC: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _parse_qr(self, s):
        try:
            p=dict(x.split(":") for x in s.split("|"))
            return {"producto":p.get("PROD","Oferta especial"),
                    "descuento":float(p.get("DESC",0.20)),
                    "base":float(p.get("BASE",9.99))}
        except: return {"producto":"Oferta especial","descuento":0.20,"base":9.99}

    # ── PANTALLAS ────────────────────────────────────────────────────────────

    def _bg(self):
        self.screen.fill(C.BG)
        t=time.time()
        for sx,sy,sp in self.stars:
            b=int(100+80*math.sin(t*sp+sx))
            pygame.draw.circle(self.screen,(b,b,b),(sx,int((sy+t*sp*8)%SCREEN_H)),1)

    def _draw_IDLE(self):
        self._bg()
        t=self.anim_t
        glow(self.screen,C.GLOW,SCREEN_W//2,SCREEN_H//2-40,130,2,35)
        bounce=int(14*math.sin(t*2.4))
        txt(self.screen,"🤖",self.F["emoji"],C.WHITE,SCREEN_W//2,SCREEN_H//2-70+bounce)
        txt(self.screen,"RoboMart",self.F["lg"],C.TEAL,SCREEN_W//2,SCREEN_H//2+75)
        a=int(155+100*math.sin(t*1.7))
        txt(self.screen,"Muéstrame  👍  para interactuar",self.F["sm"],C.GRAY,SCREEN_W//2,SCREEN_H//2+145,a)
        rrect(self.screen,C.CARD,(0,SCREEN_H-46,SCREEN_W,46),r=0)
        txt(self.screen,"● Sistema activo — navegando",self.F["xs"],C.DIM,SCREEN_W//2,SCREEN_H-23)

    def _draw_QR_SCAN(self):
        self._bg()
        t=self.anim_t
        r=int(145+18*math.sin(t*3))
        glow(self.screen,C.TEAL,SCREEN_W//2,SCREEN_H//2-20,r,3,55)
        pygame.draw.circle(self.screen,C.TEAL,(SCREEN_W//2,SCREEN_H//2-20),r,3)
        sy=SCREEN_H//2-20+int(r*math.sin(t*2.5))
        s=pygame.Surface((r*2,2),pygame.SRCALPHA); s.fill((*C.TEAL,160))
        self.screen.blit(s,(SCREEN_W//2-r,sy))
        txt(self.screen,"📷",self.F["emoji"],C.WHITE,SCREEN_W//2,SCREEN_H//2-20)
        txt(self.screen,"Muestra tu código QR a la cámara",self.F["md"],C.WHITE,SCREEN_W//2,SCREEN_H//2+148)
        a=int(155+100*math.sin(t*2))
        txt(self.screen,"Escaneando...",self.F["sm"],C.TEAL,SCREEN_W//2,SCREEN_H//2+205,a)

    def _draw_SALUDO(self):
        self._bg()
        t=self.anim_t
        rrect(self.screen,C.CARD,(SCREEN_W//2-460,60,920,580),r=24)
        rrect(self.screen,C.ORANGE,(SCREEN_W//2-460,60,920,8),r=0)
        sc=1+0.08*math.sin(t*3)
        es=self.F["xl"].render("😊",True,C.WHITE)
        es2=pygame.transform.scale(es,(int(es.get_width()*sc),int(es.get_height()*sc)))
        self.screen.blit(es2,es2.get_rect(center=(SCREEN_W//2,210)))
        txt(self.screen,"¡Hola!  ¡Aprovecha la oferta!",self.F["lg"],C.GOLD,SCREEN_W//2,345)
        if self.qr_info:
            q=self.qr_info
            rrect(self.screen,C.CARD2,(SCREEN_W//2-340,388,680,128),r=16)
            txt(self.screen,q["producto"],self.F["md"],C.TEAL,SCREEN_W//2,422)
            txt(self.screen,f"${q['base']:.2f}  →  {int(q['descuento']*100)}% OFF  →  ${q['base']*(1-q['descuento']):.2f}",self.F["sm"],C.WHITE,SCREEN_W//2,472)
        a=int(180+75*math.sin(t*2))
        txt(self.screen,"Presiona OK para ingresar tu cédula →",self.F["sm"],C.GRAY,SCREEN_W//2,SCREEN_H-55,a)

    def _draw_CEDULA(self):
        self._bg()
        t=self.anim_t
        rrect(self.screen,C.CARD,(0,0,SCREEN_W,96),r=0)
        txt(self.screen,"Ingresa tu número de cédula",self.F["md"],C.WHITE,SCREEN_W//2,48)
        n=CEDULA_DIGITS; bw=78; bh=96; gap=12
        tw=n*bw+(n-1)*gap; x0=(SCREEN_W-tw)//2; y0=SCREEN_H//2-bh//2-18
        for i in range(n):
            bx=x0+i*(bw+gap)
            if i<self.cur_pos:
                bgc=C.DGIT_DONE; brd=C.TEAL
            elif i==self.cur_pos:
                p=0.4+0.6*abs(math.sin(t*3))
                bgc=tuple(int(C.DGIT_ACT[k]*p+C.CARD[k]*(1-p)) for k in range(3))
                brd=C.ORANGE
            else:
                bgc=C.DGIT_IDLE; brd=C.DIM
            rrect(self.screen,bgc,(bx,y0,bw,bh),r=12)
            pygame.draw.rect(self.screen,brd,(bx,y0,bw,bh),2,border_radius=12)
            col=C.WHITE if i<=self.cur_pos else C.DIM
            txt(self.screen,str(self.digits[i]),self.F["digit"],col,bx+bw//2,y0+bh//2)
            if i==self.cur_pos:
                pygame.draw.rect(self.screen,C.ORANGE,(bx+8,y0+bh+7,bw-16,4),border_radius=2)
        prog=int((SCREEN_W-200)*self.cur_pos/CEDULA_DIGITS)
        rrect(self.screen,C.DIM,(100,y0+bh+28,SCREEN_W-200,5),r=3)
        if prog>0: rrect(self.screen,C.TEAL,(100,y0+bh+28,prog,5),r=3)
        rrect(self.screen,C.CARD,(0,SCREEN_H-76,SCREEN_W,76),r=0)
        txt(self.screen,"↑↓ Cambiar dígito   |   ←→ Mover cursor   |   OK / → Confirmar",self.F["xs"],C.GRAY,SCREEN_W//2,SCREEN_H-46)
        txt(self.screen,f"Posición {self.cur_pos+1} de {CEDULA_DIGITS}",self.F["xs"],C.DIM,SCREEN_W//2,SCREEN_H-20)

    def _draw_FACTURA(self):
        self._bg()
        cx,cy,cw,ch=SCREEN_W//2-370,55,740,565
        rrect(self.screen,C.CARD,(cx,cy,cw,ch),r=20)
        rrect(self.screen,C.GREEN,(cx,cy,cw,6),r=0)
        txt(self.screen,"🧾  COMPROBANTE",self.F["md"],C.GREEN,SCREEN_W//2,cy+52)
        for dx in range(0,cw-36,16):
            pygame.draw.rect(self.screen,C.DIM,(cx+18+dx,cy+88,8,2))
        rows=[("Cédula",self.cedula_str or "----------")]
        if self.qr_info:
            q=self.qr_info; pf=q["base"]*(1-q["descuento"])
            rows+=[("Producto",q["producto"]),
                   ("Precio base",f"${q['base']:.2f}"),
                   ("Descuento",f"{int(q['descuento']*100)}%")]
        yr=cy+118
        for label,val in rows:
            txt(self.screen,label,self.F["sm"],C.GRAY,cx+175,yr)
            txt(self.screen,val,self.F["sm"],C.WHITE,cx+cw-175,yr)
            yr+=52
        for dx in range(0,cw-36,16):
            pygame.draw.rect(self.screen,C.DIM,(cx+18+dx,yr+4,8,2))
        if self.qr_info:
            total=self.qr_info["base"]*(1-self.qr_info["descuento"])
            txt(self.screen,"TOTAL",self.F["md"],C.GOLD,cx+175,yr+50)
            txt(self.screen,f"${total:.2f}",self.F["md"],C.GOLD,cx+cw-175,yr+50)
        txt(self.screen,"¡Acércate a ventanilla!  🏪",self.F["lg"],C.TEAL,SCREEN_W//2,cy+ch-62)

    def _draw_EXITO(self):
        self._bg()
        for p in self.particles: p.update(); p.draw(self.screen)
        t=self.anim_t
        sc=1+0.1*abs(math.sin(t*2.2))
        es=self.F["xl"].render("🎉",True,C.WHITE)
        es2=pygame.transform.scale(es,(int(es.get_width()*sc),int(es.get_height()*sc)))
        self.screen.blit(es2,es2.get_rect(center=(SCREEN_W//2,SCREEN_H//2-65)))
        txt(self.screen,"¡Muchas gracias!",self.F["lg"],C.GOLD,SCREEN_W//2,SCREEN_H//2+75)
        txt(self.screen,"Nos vemos pronto  😊",self.F["md"],C.TEAL,SCREEN_W//2,SCREEN_H//2+148)

    # ── LOOP ─────────────────────────────────────────────────────────────────
    def run(self):
        drawers={State.IDLE:self._draw_IDLE,State.QR_SCAN:self._draw_QR_SCAN,
                 State.SALUDO:self._draw_SALUDO,State.CEDULA:self._draw_CEDULA,
                 State.FACTURA:self._draw_FACTURA,State.EXITO:self._draw_EXITO}
        try:
            while True:
                dt=self.clock.tick(FPS)/1000.0
                self.anim_t+=dt
                for event in pygame.event.get():
                    if event.type==pygame.QUIT: return
                    if event.type==pygame.KEYDOWN:
                        if event.key==pygame.K_ESCAPE: return
                        m=self._map_key(event.key)
                        if m: self.evt_q.put(m)
                try:
                    while True: self._handle_evt(self.evt_q.get_nowait())
                except Empty: pass
                self._poll_vision()
                el=self._elapsed()
                if self.state==State.QR_SCAN and el>QR_TIMEOUT_S:
                    # No leyó QR en el tiempo límite: agradece y vuelve a rodar
                    self.sounds["success"].play(); self._goto(State.EXITO)
                elif self.state==State.SALUDO  and el>10: self._goto(State.CEDULA)
                elif self.state==State.FACTURA and el>14:
                    self.sounds["success"].play(); self._goto(State.EXITO)
                elif self.state==State.EXITO  and el>7:
                    # Vuelve a reposo; _goto(IDLE) reanuda el rodado (RODAR)
                    self._goto(State.IDLE)
                if self.state==State.EXITO and not self._sound_exito_played:
                    self.sounds["success"].play(); self._sound_exito_played=True
                drawers[self.state]()
                pygame.display.flip()
        finally:
            if self.vision: self.vision.stop()
            if self.uart:
                # Frenar las ruedas al salir (Ctrl+C o ESC) antes de cerrar el puerto
                try: self.uart.send_command("PARAR")
                except Exception: pass
                self.uart.disconnect()
            pygame.quit()

if __name__=="__main__":
    RobotApp().run()
