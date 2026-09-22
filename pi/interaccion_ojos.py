#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — Interacción por gestos + OJOS + VOZ en el celular
=============================================================================
Flujo (todo en la pantalla del robot, que son los OJOS):

  REPOSO ── nadie cerca ──► duerme y, cada tanto, invita: "¡Bienvenido a ESPOL!"
     │
     └── aparece una persona ──► SALUDA y arranca la CHARLA:
            quiénes somos (PhyCom) → qué hacemos → unite al club
         La charla se corta sola si la persona se va o hace un gesto.

  GESTOS de la mano (los detecta la PC):
     · PULGAR ARRIBA  → ESTADO 1: QR para cargar tus datos (con instrucciones
                        paso a paso en el celular)
     · PALMA ABIERTA  → ESTADO 2: busca un QR (15 s) y muestra la promoción

  …y en todos los casos termina con "¡Gracias!" y vuelve a los ojos.

LA VOZ SALE POR EL CELULAR
El robot no tiene parlante. Levanta un servidor (voz_server.py) y el celular,
conectado al mismo hotspot, abre http://<IP-de-la-Pi>:8090 : ahí se escucha lo
que el robot dice, se ve su cara y salen las instrucciones del paso en curso.
En la pantalla del robot aparece un QR para entrar sin escribir la IP.

Reutiliza lo que YA funciona, sin tocarlo:
  · ojos.py           → los ojos expresivos (ahora se ajustan solos al panel)
  · dialogos.py       → el guion: todo lo que dice, en un solo archivo
  · voz_server.py     → la voz y las instrucciones en el celular
  · pi_qr.py          → dibuja el QR en pantalla
  · pi_zmq_client.py  → manda frames a la PC y recibe thumbs_up / palma / qr_data
La detección de gestos y QR corre en la PC (pc_server.py). No hay cambios ahí.

Ejecutar en la Pi, con el panel conectado y la PC (robot_server.py) andando:
    source ~/mi_proyecto_env/bin/activate
    ROBOT_PC_IP=IP_DE_LA_PC DISPLAY=:0 python3 ~/robot_bipedo/pi/interaccion_ojos.py

Teclas: 1 = forzar estado 1 (QR datos)     2 = forzar estado 2 (leer QR)
        C = charla completa                V = mostrar el QR de la voz
        ESC o Q = salir
=============================================================================
"""

import os
import sys
import json
import socket
import time
from pathlib import Path

import pygame

sys.path.insert(0, str(Path(__file__).parent))
from ojos import Ojos                       # noqa: E402
from pi_qr import GeneradorQR               # noqa: E402
from pi_zmq_client import VisionClient      # noqa: E402
import dialogos                             # noqa: E402
from voz_server import VozServer            # noqa: E402

# ═══════════════════ CONFIGURACIÓN ═══════════════════
try:
    CFG = json.loads((Path(__file__).parent.parent / "config.json").read_text("utf-8"))
except Exception:
    CFG = {}
_P = CFG.get("pantalla", {})
ANCHO      = _P.get("ancho", 1280)
ALTO       = _P.get("alto", 720)
FULLSCREEN = _P.get("fullscreen", True)
PC_IP      = os.environ.get("ROBOT_PC_IP") or CFG.get("red", {}).get("pc_ip", "192.168.43.50")

# URL a la que apunta el QR del estado 1. Prioridad (un solo lugar para cambiarla):
#   1) variable de entorno ROBOT_URL_ENCUESTA  → p.ej. la URL de Ngrok con datos móviles
#      ROBOT_URL_ENCUESTA=https://xxxx.ngrok-free.dev/encuesta python3 pi/interaccion_ojos.py
#   2) config.json → interaccion.url_encuesta
#   3) automático al hotspot: http://<PC>:<api>/encuesta  (la PC guarda en SQLite, sin internet)
_API_PORT      = CFG.get("red", {}).get("api_puerto", 8000)
URL_ENCUESTA   = (os.environ.get("ROBOT_URL_ENCUESTA")
                  or CFG.get("interaccion", {}).get("url_encuesta")
                  or f"http://{PC_IP}:{_API_PORT}/encuesta")
SEG_ESTADO     = 15      # duración de cada estado (búsqueda/muestra de QR)
SEG_PROMO      = 6       # cuánto se muestra la promo una vez hallado el QR
CONFIRM_GESTO  = 3       # lecturas seguidas del mismo gesto para confirmarlo
ENFRIAMIENTO   = 2.0     # s de espera antes de volver a disparar

# ── Voz en el celular ────────────────────────────────────────────────────────
VOZ_PUERTO   = int(os.environ.get("ROBOT_VOZ_PUERTO",
                                  CFG.get("interaccion", {}).get("voz_puerto", 8090)))
VOZ_ACTIVA   = os.environ.get("ROBOT_VOZ", "1") != "0"
# Cada cuánto, estando solo, suelta una frase de atracción
SEG_ATRACCION = 25.0
# Cuánto se muestra el QR de la voz al arrancar (y con la tecla V)
SEG_QR_VOZ    = 12.0

# Mapa QR → promoción. La clave se busca como subcadena dentro del contenido del QR.
PROMOS = {
    "agua":    ("3 aguas al precio de 2", "hidratate y ahorra"),
    "gaseosa": ("2x1 en gaseosas",        "lleva dos, paga una"),
    "snack":   ("30% off en snacks",      "proba los nuevos sabores"),
}
PROMO_DEFAULT = ("Promocion especial", "gracias por participar")

def promo_para(qr: str):
    q = (qr or "").lower()
    for clave, val in PROMOS.items():
        if clave in q:
            return val
    return PROMO_DEFAULT


def promo_desde_pc(qr_data: str):
    """
    Pregunta a la PC el descuento REAL del código leído (los QR generados en el
    dashboard viven en la base de la PC) y consume un uso. Si la PC no responde,
    cae al mapa local PROMOS para no quedarse mudo.
    """
    import urllib.request
    import urllib.parse
    try:
        url = (f"http://{PC_IP}:{_API_PORT}/api/qr/promo?codigo="
               + urllib.parse.quote(qr_data or ""))
        with urllib.request.urlopen(url, timeout=1.5) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("valido"):
            return (f"{d['descuento_pct']}% OFF en {d['nombre']}",
                    "¡Aprovechá tu descuento!")
        return ("Código no válido", "probá con otro")
    except Exception:
        return promo_para(qr_data)


def mi_ip() -> str:
    """IP de esta Pi dentro del hotspot (la que va en el QR de la voz)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((PC_IP, 1))          # no manda nada: solo elige la interfaz
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# Colores para el texto sobre la pantalla oscura
CLARO = (232, 240, 244)
GRIS  = (138, 160, 173)
VERDE = (120, 224, 180)


# ═══════════════════════════════════════════════════════════════════════════
# LOCUTOR — recita un guion sin bloquear el bucle de dibujo
# ═══════════════════════════════════════════════════════════════════════════
class Locutor:
    """
    Va pasando las líneas de un guion a su ritmo. En cada cuadro se le pregunta
    `actualizar()`; él se encarga de cambiar la emoción de los ojos, lanzar el
    gesto y publicar la frase en el celular.

    No usa hilos ni sleeps: el bucle de pygame tiene que seguir dibujando a
    30 fps mientras el robot habla, o los ojos se congelan a media frase.
    """

    def __init__(self, ojos: Ojos, voz: VozServer):
        self.ojos = ojos
        self.voz  = voz
        self._lineas = []
        self._i = -1
        self._hasta = 0.0
        self.fase = ""

    @property
    def hablando(self) -> bool:
        return self._i >= 0

    @property
    def texto(self) -> str:
        if 0 <= self._i < len(self._lineas):
            return self._lineas[self._i]["texto"]
        return ""

    def arrancar(self, lineas: list, fase: str = "", instrucciones: list = None):
        self._lineas = list(lineas or [])
        self._i = -1
        self._hasta = 0.0
        self.fase = fase
        if self.voz and instrucciones is not None:
            self.voz.instrucciones(instrucciones, fase=fase)

    def cortar(self):
        self._lineas = []
        self._i = -1

    def actualizar(self, ahora: float):
        """Avanza a la línea siguiente cuando toca. Devuelve True si sigue hablando."""
        if not self._lineas:
            self._i = -1
            return False
        if ahora < self._hasta:
            return True

        self._i += 1
        if self._i >= len(self._lineas):          # se terminó el guion
            self._lineas = []
            self._i = -1
            return False

        linea = self._lineas[self._i]
        self.ojos.set_animo(linea.get("animo", Ojos.HABLANDO))
        if linea.get("gesto"):
            self.ojos.gesto(linea["gesto"])
        if self.voz:
            self.voz.publicar(linea, fase=self.fase)
        self._hasta = ahora + linea.get("dur", 3.0)
        return True


def main():
    global ANCHO, ALTO
    pygame.init()
    # Pantalla completa REAL: con (0,0) + FULLSCREEN se usa la resolución NATIVA del
    # panel y se llena TODA la pantalla.
    if FULLSCREEN:
        pantalla = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        ANCHO, ALTO = pantalla.get_size()      # tamaño real del panel
    else:
        pantalla = pygame.display.set_mode((ANCHO, ALTO))
    pygame.display.set_caption("Robot Pingüino — interacción")
    pygame.mouse.set_visible(False)
    reloj = pygame.time.Clock()

    # Rotación del panel (por si el Tontec va montado girado). 0/90/180/270 en config.json.
    ROT = int(CFG.get("pantalla", {}).get("rotacion", 0)) % 360
    if ROT in (90, 270):
        LW, LH = ALTO, ANCHO          # lienzo lógico con ancho/alto intercambiados
    else:
        LW, LH = ANCHO, ALTO
    sup = pygame.Surface((LW, LH))    # se dibuja acá y al final se rota a la pantalla
    print(f"[OJOS] Panel {ANCHO}x{ALTO} · lienzo {LW}x{LH} · rotación {ROT}°")

    f_grande = pygame.font.SysFont("dejavusans", int(LH * 0.075), bold=True)
    f_chico  = pygame.font.SysFont("dejavusans", int(LH * 0.042))
    f_mini   = pygame.font.SysFont("dejavusans", max(11, int(LH * 0.028)))

    # ── Los ojos se dimensionan SOLOS al panel ───────────────────────────────
    # Dos tamaños: a pantalla completa (sin texto) y dejando la franja de abajo
    # libre cuando hay que escribir algo. Antes esto era una escala fija (LH/450)
    # y en el Tontec los ojos quedaban chicos y corridos a una esquina.
    ojos  = Ojos()
    ALTO_CON_TEXTO = LH * 0.62
    ALTO_SIN_TEXTO = LH * 0.86
    _modo_texto = [None]      # lista de 1 para poder tocarla desde la función

    def modo_ojos(con_texto: bool):
        """Redimensiona los ojos solo cuando cambia el modo (es barato, pero
        no hace falta recalcularlo 30 veces por segundo)."""
        if _modo_texto[0] is con_texto:
            return
        _modo_texto[0] = con_texto
        ojos.ajustar_a(LW, ALTO_CON_TEXTO if con_texto else ALTO_SIN_TEXTO)

    modo_ojos(False)
    CY_SIN_TEXTO = int(LH * 0.50)
    CY_CON_TEXTO = int(LH * 0.40)

    genqr = GeneradorQR()

    # ── Voz en el celular ────────────────────────────────────────────────────
    voz = None
    URL_VOZ = ""
    if VOZ_ACTIVA:
        try:
            voz = VozServer(VOZ_PUERTO).start()
            URL_VOZ = f"http://{mi_ip()}:{VOZ_PUERTO}"
            print(f"[OJOS] Voz del robot en el celular: {URL_VOZ}")
        except Exception as e:
            print(f"[OJOS] OJO: no pude levantar la voz ({e}). Sigo sin ella.")
            voz = None

    locutor = Locutor(ojos, voz)

    vc = VisionClient(pc_ip=PC_IP)
    vc.start()
    vc.set_mode("TODO")               # reposo: detecta PERSONA + gestos a la vez

    def texto(fuente, cadena, y, color=CLARO):
        img = fuente.render(cadena, True, color)
        sup.blit(img, img.get_rect(center=(LW // 2, int(y))))

    def texto_ajustado(cadena, y, color=CLARO):
        """Escribe una frase del guion partiéndola en renglones si no entra."""
        palabras = cadena.split()
        renglones, actual = [], ""
        for p in palabras:
            prueba = (actual + " " + p).strip()
            if f_chico.size(prueba)[0] <= LW * 0.92:
                actual = prueba
            else:
                if actual:
                    renglones.append(actual)
                actual = p
        if actual:
            renglones.append(actual)
        renglones = renglones[-3:]            # 3 renglones como máximo
        alto_r = f_chico.get_height() + 4
        y0 = y - (len(renglones) - 1) * alto_r / 2
        for i, r in enumerate(renglones):
            texto(f_chico, r, y0 + i * alto_r, color)

    def barra(elapsed):
        w = int(min(1.0, elapsed / SEG_ESTADO) * LW)
        pygame.draw.rect(sup, Ojos.OJO, (0, LH - 6, w, 6))

    def indicador(ok):
        """Puntito de estado arriba a la izquierda: verde=visión ok, rojo=sin visión."""
        r = max(4, int(LH * 0.012)); cx = int(LH * 0.035); cy = int(LH * 0.035)
        pygame.draw.circle(sup, (80, 220, 120) if ok else (220, 90, 90), (cx, cy), r)
        img = f_mini.render("visión ok" if ok else "sin visión", True, GRIS)
        sup.blit(img, (cx + r + 8, cy - img.get_height() // 2))

    estado    = "REPOSO"
    t_estado  = time.time()
    siguiente = None
    c_pulgar  = c_palma = 0
    ultimo_fin = 0.0
    ultima_persona = 0.0
    ultimo_res = 0.0
    ultima_atraccion = 0.0
    promo = ("", "")
    t_qr_voz = time.time() if voz else 0.0    # el QR de la voz se muestra al arrancar

    if voz:
        voz.instrucciones([
            "Conectate al mismo WiFi que el robot.",
            "Tocá «Activar voz» y subí el volumen.",
            "Dejá esta página abierta: acá te hablo.",
        ], fase="bienvenida")

    corriendo = True
    while corriendo:
        dt = reloj.tick(30) / 1000.0
        ahora = time.time()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                corriendo = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    corriendo = False
                elif ev.key == pygame.K_1 and estado == "REPOSO":
                    siguiente = "ESTADO1"; estado = "REACCION"; t_estado = ahora
                elif ev.key == pygame.K_2 and estado == "REPOSO":
                    siguiente = "ESTADO2"; estado = "REACCION"; t_estado = ahora
                elif ev.key == pygame.K_c:
                    locutor.arrancar(dialogos.CHARLA_COMPLETA, "charla")
                    estado = "CHARLA"; t_estado = ahora
                elif ev.key == pygame.K_v and voz:
                    t_qr_voz = ahora

        res = vc.get_last_result()
        if res:
            ultimo_res = ahora
            if res.get("persona_detectada"):
                ultima_persona = ahora

        # Mirada: prioridad mano > persona > frente
        if res and res.get("hand_x") is not None:
            ojos.mirar(res["hand_x"], res["hand_y"])
        elif res and res.get("persona_x") is not None:
            ojos.mirar(res["persona_x"], res["persona_y"])
        elif estado == "REPOSO":
            ojos.mirar_al_frente()

        hay_persona = (ahora - ultima_persona) < 4.0
        vision_ok   = (ahora - ultimo_res) < 2.0
        mostrando_qr_voz = voz and (ahora - t_qr_voz) < SEG_QR_VOZ

        # ── MÁQUINA DE ESTADOS ──
        if estado == "REPOSO":
            # Despierta si hay alguien; si está solo un rato, se "duerme".
            if not locutor.hablando:
                ojos.set_animo(Ojos.NEUTRO if hay_persona else Ojos.DORMIDO)

            # Llegó alguien → saluda y arranca la charla de PhyCom.
            if hay_persona and not locutor.hablando and ahora - ultimo_fin > ENFRIAMIENTO:
                locutor.arrancar(
                    dialogos.SALUDO + dialogos.PHYCOM + dialogos.ACTIVIDADES
                    + dialogos.INVITACION,
                    "charla",
                    instrucciones=[
                        "Mostrá el PULGAR ARRIBA para dejarme tus datos.",
                        "Mostrá la PALMA ABIERTA para canjear un código.",
                    ])
                estado = "CHARLA"; t_estado = ahora

            # Sin nadie: cada tanto suelta una frase de atracción.
            elif (not hay_persona and not locutor.hablando
                  and ahora - ultima_atraccion > SEG_ATRACCION):
                ultima_atraccion = ahora
                locutor.arrancar(dialogos.ATRACCION, "atraccion")

            if ahora - ultimo_fin > ENFRIAMIENTO and res:
                c_pulgar = c_pulgar + 1 if res.get("thumbs_up") else 0
                c_palma  = c_palma + 1 if res.get("hand_detected") else 0
                if c_pulgar >= CONFIRM_GESTO:
                    c_pulgar = c_palma = 0; locutor.cortar()
                    siguiente = "ESTADO1"; estado = "REACCION"; t_estado = ahora
                elif c_palma >= CONFIRM_GESTO:
                    c_pulgar = c_palma = 0; locutor.cortar()
                    siguiente = "ESTADO2"; estado = "REACCION"; t_estado = ahora

        elif estado == "CHARLA":
            # Habla mientras haya alguien. Un gesto la interrumpe.
            if res:
                c_pulgar = c_pulgar + 1 if res.get("thumbs_up") else 0
                c_palma  = c_palma + 1 if res.get("hand_detected") else 0
                if c_pulgar >= CONFIRM_GESTO:
                    c_pulgar = c_palma = 0; locutor.cortar()
                    siguiente = "ESTADO1"; estado = "REACCION"; t_estado = ahora
                elif c_palma >= CONFIRM_GESTO:
                    c_pulgar = c_palma = 0; locutor.cortar()
                    siguiente = "ESTADO2"; estado = "REACCION"; t_estado = ahora

            if not hay_persona and ahora - t_estado > 6.0:
                locutor.cortar()             # se fue: no le hablamos a la nada
                estado = "REPOSO"; t_estado = ahora; ultimo_fin = ahora
            elif not locutor.hablando and estado == "CHARLA":
                estado = "GRACIAS"; t_estado = ahora

        elif estado == "REACCION":
            ojos.set_animo(Ojos.ESTRELLADO)
            if voz and ahora - t_estado < dt * 2:      # solo al entrar
                voz.decir("¡Te vi!", animo=Ojos.ESTRELLADO,
                          gesto="saltar", sonido="chispa", fase="reaccion")
            if ahora - t_estado > 1.3:
                if siguiente == "ESTADO1":
                    estado = "ESTADO1"; t_estado = ahora
                    locutor.arrancar(dialogos.INSTRUCCIONES_QR
                                     + dialogos.INSTRUCCIONES_FORM,
                                     "instrucciones",
                                     instrucciones=[
                                         "Apuntá la cámara del celular al QR de mi pantalla.",
                                         "Tocá el enlace que aparece.",
                                         "Completá tus datos (30 segundos).",
                                         "Listo: te llega la info del club.",
                                     ])
                else:
                    estado = "ESTADO2_BUSCA"; t_estado = ahora; vc.set_mode("QR")
                    if voz:
                        voz.decir("Mostrame tu código, lo estoy buscando.",
                                  animo=Ojos.SORPRENDIDO, sonido="pop",
                                  fase="leyendo QR")
                        voz.instrucciones([
                            "Poné tu código QR frente a mi cámara.",
                            "Sostenelo quieto, a unos 40 cm.",
                            "Tenés 15 segundos.",
                        ])

        elif estado == "ESTADO1":
            if ahora - t_estado > SEG_ESTADO:
                locutor.cortar()
                estado = "GRACIAS"; t_estado = ahora

        elif estado == "ESTADO2_BUSCA":
            if res and res.get("qr_data"):
                promo = promo_desde_pc(res["qr_data"]); vc.set_mode("TODO")
                estado = "ESTADO2_PROMO"; t_estado = ahora
                if voz:
                    voz.decir(f"{promo[0]}. {promo[1]}", animo=Ojos.ESTRELLADO,
                              gesto="saltar", sonido="tada", fase="tu promoción")
            elif ahora - t_estado > SEG_ESTADO:
                vc.set_mode("TODO"); promo = ("No vi ningun codigo", "probemos de nuevo")
                estado = "ESTADO2_PROMO"; t_estado = ahora
                if voz:
                    voz.decir("No llegué a ver el código. Probemos de nuevo.",
                              animo=Ojos.TRISTE, gesto="negar", fase="sin código")

        elif estado == "ESTADO2_PROMO":
            if ahora - t_estado > SEG_PROMO:
                estado = "GRACIAS"; t_estado = ahora

        elif estado == "GRACIAS":
            if not locutor.hablando and ahora - t_estado < dt * 2:
                locutor.arrancar(dialogos.DESPEDIDA, "gracias", instrucciones=[])
            if not locutor.hablando and ahora - t_estado > 2.2:
                estado = "REPOSO"; t_estado = ahora; ultimo_fin = ahora
                ojos.set_animo(Ojos.NEUTRO)

        # El locutor manda sobre la emoción de los ojos mientras habla.
        locutor.actualizar(ahora)
        ojos.actualizar(dt)

        # ── DIBUJO (sobre el lienzo lógico 'sup') ──
        sup.fill(Ojos.FONDO)

        # QR para entrar a la voz desde el celular (al arrancar y con la tecla V)
        if mostrando_qr_voz and estado in ("REPOSO", "CHARLA"):
            texto(f_grande, "Escuchame en tu celular", LH * 0.12)
            genqr.dibujar(sup, URL_VOZ, (LW // 2, int(LH * 0.52)),
                          int(LH * 0.50), f_chico)
            texto(f_chico, URL_VOZ, LH * 0.90, GRIS)

        elif estado in ("REPOSO", "REACCION", "GRACIAS", "CHARLA"):
            hay_texto = bool(locutor.texto) or estado in ("REACCION", "GRACIAS") \
                        or (estado == "REPOSO" and hay_persona)
            modo_ojos(hay_texto)
            ojos.dibujar(sup, LW // 2, CY_CON_TEXTO if hay_texto else CY_SIN_TEXTO)

            if locutor.texto:
                texto_ajustado(locutor.texto, LH * 0.84)
            elif estado == "REACCION":
                texto(f_grande, "¡Hola!", LH * 0.84)
            elif estado == "GRACIAS":
                texto(f_grande, "¡Gracias!", LH * 0.84)
            elif estado == "REPOSO" and hay_persona:
                texto(f_chico, "PULGAR = tus datos    ·    PALMA = una oferta", LH * 0.86)

        elif estado == "ESTADO1":
            texto(f_grande, "Escaneá y cargá tus datos", LH * 0.12)
            genqr.dibujar(sup, URL_ENCUESTA, (LW // 2, int(LH * 0.50)), int(LH * 0.50), f_chico)
            if locutor.texto:
                texto_ajustado(locutor.texto, LH * 0.88, GRIS)
            else:
                texto(f_chico, "te lleva a un formulario en tu celular", LH * 0.88, GRIS)
            barra(ahora - t_estado)

        elif estado == "ESTADO2_BUSCA":
            modo_ojos(True)
            ojos.dibujar(sup, LW // 2, CY_CON_TEXTO)
            texto(f_grande, "Mostrame tu código", LH * 0.80)
            texto(f_chico, "la cámara está buscando un QR", LH * 0.90, GRIS)
            barra(ahora - t_estado)

        elif estado == "ESTADO2_PROMO":
            modo_ojos(True)
            ojos.dibujar(sup, LW // 2, CY_CON_TEXTO)
            texto(f_grande, "¡" + promo[0] + "!", LH * 0.80, VERDE)
            texto(f_chico, promo[1], LH * 0.90, GRIS)

        indicador(vision_ok)

        # Volcar a la pantalla (rotando si el panel va montado girado)
        pantalla.fill(Ojos.FONDO)
        if ROT:
            rot = pygame.transform.rotate(sup, ROT)
            pantalla.blit(rot, rot.get_rect(center=(ANCHO // 2, ALTO // 2)))
        else:
            pantalla.blit(sup, (0, 0))
        pygame.display.flip()

    vc.stop()
    if voz:
        voz.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
