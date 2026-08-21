#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — Interacción por gestos + OJOS (pantalla/cámara)
=============================================================================
Flujo (todo en la pantalla del robot, que son los OJOS):

  REPOSO ── la cámara busca un gesto de la mano ──►
     · PULGAR ARRIBA  → ESTADO 1: muestra un QR para cargar tus datos (15 s)
     · PALMA ABIERTA  → ESTADO 2: busca un QR (15 s); si lo halla, muestra la
                         promoción correspondiente
  …y en los dos casos, al terminar dice "¡Gracias!" y vuelve a los ojos.

Reutiliza lo que YA funciona, sin tocarlo:
  · ojos.py           → los ojos expresivos
  · pi_qr.py          → dibuja el QR en pantalla
  · pi_zmq_client.py  → manda frames a la PC y recibe thumbs_up / palma / qr_data
La detección de gestos y QR corre en la PC (pc_server.py). No hay cambios ahí.

Ejecutar en la Pi, con el monitor HDMI conectado y la PC (robot_server.py) andando:
    source ~/mi_proyecto_env/bin/activate
    ROBOT_PC_IP=IP_DE_LA_PC DISPLAY=:0 python3 ~/robot_bipedo/pi/interaccion_ojos.py

Teclas: 1 = forzar estado 1, 2 = forzar estado 2 (para probar sin cámara),
        ESC o Q = salir.
=============================================================================
"""

import os
import sys
import json
import time
from pathlib import Path

import pygame

sys.path.insert(0, str(Path(__file__).parent))
from ojos import Ojos                       # noqa: E402
from pi_qr import GeneradorQR               # noqa: E402
from pi_zmq_client import VisionClient      # noqa: E402

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

# Colores para el texto sobre la pantalla oscura
CLARO = (232, 240, 244)
GRIS  = (138, 160, 173)
VERDE = (120, 224, 180)


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

    f_grande = pygame.font.SysFont("dejavusans", int(LH * 0.070), bold=True)
    f_chico  = pygame.font.SysFont("dejavusans", int(LH * 0.035))
    f_mini   = pygame.font.SysFont("dejavusans", max(11, int(LH * 0.026)))
    esc_ojos = LH / 450.0

    ojos  = Ojos()
    genqr = GeneradorQR()

    vc = VisionClient(pc_ip=PC_IP)
    vc.start()
    vc.set_mode("TODO")               # reposo: detecta PERSONA + gestos a la vez

    def texto(fuente, cadena, y, color=CLARO):
        img = fuente.render(cadena, True, color)
        sup.blit(img, img.get_rect(center=(LW // 2, int(y))))

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
    promo = ("", "")

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

        # ── MÁQUINA DE ESTADOS ──
        if estado == "REPOSO":
            # Despierta si hay alguien; si está solo un rato, se "duerme".
            ojos.set_animo(Ojos.NEUTRO if hay_persona else Ojos.DORMIDO)
            if ahora - ultimo_fin > ENFRIAMIENTO and res:
                c_pulgar = c_pulgar + 1 if res.get("thumbs_up") else 0
                c_palma  = c_palma + 1 if res.get("hand_detected") else 0
                if c_pulgar >= CONFIRM_GESTO:
                    c_pulgar = c_palma = 0; siguiente = "ESTADO1"; estado = "REACCION"; t_estado = ahora
                elif c_palma >= CONFIRM_GESTO:
                    c_pulgar = c_palma = 0; siguiente = "ESTADO2"; estado = "REACCION"; t_estado = ahora

        elif estado == "REACCION":
            ojos.set_animo(Ojos.ESTRELLADO)
            if ahora - t_estado > 1.3:
                if siguiente == "ESTADO1":
                    estado = "ESTADO1"; t_estado = ahora
                else:
                    estado = "ESTADO2_BUSCA"; t_estado = ahora; vc.set_mode("QR")

        elif estado == "ESTADO1":
            if ahora - t_estado > SEG_ESTADO:
                estado = "GRACIAS"; t_estado = ahora

        elif estado == "ESTADO2_BUSCA":
            if res and res.get("qr_data"):
                promo = promo_desde_pc(res["qr_data"]); vc.set_mode("TODO")
                estado = "ESTADO2_PROMO"; t_estado = ahora
            elif ahora - t_estado > SEG_ESTADO:
                vc.set_mode("TODO"); promo = ("No vi ningun codigo", "probemos de nuevo")
                estado = "ESTADO2_PROMO"; t_estado = ahora

        elif estado == "ESTADO2_PROMO":
            if ahora - t_estado > SEG_PROMO:
                estado = "GRACIAS"; t_estado = ahora

        elif estado == "GRACIAS":
            ojos.set_animo(Ojos.FELIZ)
            if ahora - t_estado > 2.2:
                estado = "REPOSO"; t_estado = ahora; ultimo_fin = ahora
                ojos.set_animo(Ojos.NEUTRO)

        ojos.actualizar(dt)

        # ── DIBUJO (sobre el lienzo lógico 'sup') ──
        sup.fill(Ojos.FONDO)

        if estado in ("REPOSO", "REACCION", "GRACIAS"):
            ojos.dibujar(sup, LW // 2, int(LH * 0.42), escala=esc_ojos)
            if estado == "REACCION":
                texto(f_grande, "¡Hola!", LH * 0.82)
            elif estado == "GRACIAS":
                texto(f_grande, "¡Gracias!", LH * 0.82)
            elif estado == "REPOSO" and hay_persona:
                texto(f_chico, "PULGAR = tus datos    ·    PALMA = una oferta", LH * 0.86)

        elif estado == "ESTADO1":
            texto(f_grande, "Escaneá y cargá tus datos", LH * 0.15)
            genqr.dibujar(sup, URL_ENCUESTA, (LW // 2, LH // 2), int(LH * 0.52), f_chico)
            texto(f_chico, "te lleva a un formulario en tu celular", LH * 0.88, GRIS)
            barra(ahora - t_estado)

        elif estado == "ESTADO2_BUSCA":
            texto(f_grande, "Mostrame tu código", LH * 0.42)
            texto(f_chico, "la cámara está buscando un QR", LH * 0.55, GRIS)
            barra(ahora - t_estado)

        elif estado == "ESTADO2_PROMO":
            texto(f_grande, "¡" + promo[0] + "!", LH * 0.42, VERDE)
            texto(f_chico, promo[1], LH * 0.55, GRIS)

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
    pygame.quit()


if __name__ == "__main__":
    main()
