#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — Los OJOS
=============================================================================
La cara del robot ahora son SOLO ojos (la pantalla va montada como los ojos de
la maqueta). Ojos expresivos dibujados con primitivas de Pygame — nada de
imágenes, así escala igual en el HDMI de pruebas y en el panel del robot.

Emociones (self.animo):
    NEUTRO       ojos tranquilos, parpadean y miran a los lados
    FELIZ        ojos en media luna (sonrisa)
    ENOJADO      párpado superior inclinado hacia adentro (ceño)
    SORPRENDIDO  ojos grandes y bien abiertos
    TRISTE       párpado inclinado hacia afuera, mirada caída
    ESTRELLADO   ojos con forma de estrella (al detectar / dar la oferta)
    DORMIDO      ojos casi cerrados (reposo)
    AMOR         corazones (al invitar a unirse a PhyCom)
    HABLANDO     ojos atentos con pulso suave, acompaña a la voz del celular

Gestos (animaciones cortas que se superponen a la emoción):
    guinar(lado)     cierra un ojo un instante
    asentir()        los ojos bajan y suben — "sí"
    negar()          los ojos van y vienen de lado — "no"
    saltar()         un brinco de entusiasmo
    mirar_alrededor()  barre la mirada de un lado al otro

Técnica: cada ojo es un rectángulo redondeado. La emoción se logra tapando
parte del ojo con "párpados" del color del fondo (triángulos o una elipse).
Es como se hacen los ojos de robot y se lee clarísimo en pantallas chicas.

    ojos = Ojos()
    ojos.ajustar_a(ANCHO, ALTO)   # ← llena el panel, sea 480x320 o 1024x600
    ojos.set_animo(Ojos.ENOJADO)
    ojos.mirar(0.8, 0.4)          # 0=izq, 0.5=centro, 1=der (coords de la cámara)
    ojos.guinar()
    ojos.actualizar(dt)
    ojos.dibujar(pantalla, cx, cy)

Asume FONDO oscuro (pantalla dedicada a los ojos). El color de fondo es Ojos.FONDO.

─────────────────────────────────────────────────────────────────────────────
POR QUÉ `ajustar_a()`
─────────────────────────────────────────────────────────────────────────────
Antes los tamaños eran fijos (180 px de ojo, 250 de separación) y quien los
usaba tenía que adivinar una `escala`. En el Tontec de 9x16 cm eso dejaba los
ojos chiquitos y corridos hacia una esquina. Ahora se le pasa el tamaño real
del panel y los ojos se dimensionan solos para llenarlo, con el mismo aspecto
en cualquier pantalla.
=============================================================================
"""

import math
import random

import pygame


class Ojos:
    # ── Emociones ─────────────────────────────────────────────────────────────
    NEUTRO      = "neutro"
    FELIZ       = "feliz"
    ENOJADO     = "enojado"
    SORPRENDIDO = "sorprendido"
    TRISTE      = "triste"
    ESTRELLADO  = "estrellado"
    DORMIDO     = "dormido"
    AMOR        = "amor"
    HABLANDO    = "hablando"

    # ── Colores ───────────────────────────────────────────────────────────────
    OJO      = (64, 224, 255)     # cian brillante
    FONDO    = (6, 10, 18)        # el fondo de la pantalla de los ojos
    ESTRELLA = (255, 216, 77)     # ojos de estrella
    CORAZON  = (255, 96, 128)     # ojos de corazón (AMOR)

    # ── Proporciones sobre el panel (las usa ajustar_a) ───────────────────────
    # Los dos ojos + el hueco del medio ocupan este % del ancho de la pantalla.
    PCT_ANCHO = 0.88
    # Alto del ojo como % del alto de la pantalla.
    PCT_ALTO  = 0.60
    # Hueco entre los ojos, como fracción del ancho de UN ojo.
    HUECO     = 0.42

    def __init__(self, ancho=180, alto=180, radio=52, sep=250):
        self.animo   = self.NEUTRO
        self.base_w  = ancho      # ancho base del ojo (px a escala 1.0)
        self.base_h  = alto       # alto base del ojo
        self.radio   = radio      # redondeo de esquinas
        self.sep     = sep        # separación entre centros de los ojos

        # Recorrido de la mirada en px (lo recalcula ajustar_a según el panel)
        self.rango_x = 24
        self.rango_y = 15

        self._t = 0.0
        # Mirada (0..1), con suavizado
        self._mx = 0.5; self._my = 0.5
        self._mxs = 0.5; self._mys = 0.5
        # Parpadeo
        self._parp = 0.0
        self._prox = self._sortear()
        # Factores de tamaño animados (para transiciones suaves entre emociones)
        self._wf = 1.0; self._hf = 1.0

        # Gestos: (nombre, tiempo_restante, duración_total) o None
        self._gesto = None
        self._gesto_t = 0.0
        self._gesto_dur = 0.0
        self._gesto_lado = 1

    # ── Ajuste al panel ───────────────────────────────────────────────────────

    def ajustar_a(self, ancho: int, alto: int,
                  pct_ancho: float = None, pct_alto: float = None):
        """
        Dimensiona los ojos para LLENAR una pantalla de `ancho` x `alto`.

        Reparte el ancho disponible entre: ojo + hueco + ojo, y recorta el alto
        del ojo para que quepa en la pantalla con aire arriba y abajo. Después
        de llamar a esto, `dibujar()` se usa con escala 1.0.

        Devuelve (ancho_ojo, alto_ojo, separación) por si el que llama los
        quiere para posicionar otras cosas.
        """
        pa = self.PCT_ANCHO if pct_ancho is None else pct_ancho
        ph = self.PCT_ALTO  if pct_alto  is None else pct_alto

        # Ancho: 2 ojos + 1 hueco (el hueco es una fracción del ojo)
        util_w = ancho * pa
        w = util_w / (2.0 + self.HUECO)

        # Alto: lo que pida el porcentaje, pero sin dejar ojos absurdamente
        # alargados (un ojo más alto que ancho x1.35 se lee como una barra).
        h = min(alto * ph, w * 1.35)

        self.base_w = w
        self.base_h = h
        self.sep    = w * (1.0 + self.HUECO)      # centro a centro
        self.radio  = min(w, h) * 0.30            # esquinas proporcionales

        # La mirada se mueve dentro del aire que queda a los costados.
        self.rango_x = max(6.0, (ancho - (self.sep + w)) * 0.42)
        self.rango_y = max(4.0, (alto - h) * 0.22)
        return self.base_w, self.base_h, self.sep

    # ── API ───────────────────────────────────────────────────────────────────

    def set_animo(self, animo: str):
        self.animo = animo

    def mirar(self, x: float, y: float = 0.5):
        self._mx = max(0.0, min(1.0, float(x)))
        self._my = max(0.0, min(1.0, float(y)))

    def mirar_al_frente(self):
        self._mx = 0.5; self._my = 0.5

    def parpadear(self):
        """Fuerza un parpadeo ya."""
        self._prox = 0.0

    # ── Gestos ────────────────────────────────────────────────────────────────

    def gesto(self, nombre: str, dur: float = None, lado: int = 1):
        """
        Lanza un gesto corto. Se superpone a la emoción actual y se apaga solo.
        Gestos: "guino", "asentir", "negar", "saltar", "alrededor".
        """
        duraciones = {"guino": 0.45, "asentir": 0.9, "negar": 0.9,
                      "saltar": 0.7, "alrededor": 2.0}
        if nombre not in duraciones:
            return
        self._gesto      = nombre
        self._gesto_dur  = dur if dur else duraciones[nombre]
        self._gesto_t    = 0.0
        self._gesto_lado = lado

    def guinar(self, lado: int = 1):   self.gesto("guino", lado=lado)
    def asentir(self):                 self.gesto("asentir")
    def negar(self):                   self.gesto("negar")
    def saltar(self):                  self.gesto("saltar")
    def mirar_alrededor(self):         self.gesto("alrededor")

    @property
    def gesto_activo(self) -> bool:
        return self._gesto is not None

    # ── Actualización ──────────────────────────────────────────────────────────

    def _targets(self):
        """(ancho, alto) objetivo del ojo según la emoción."""
        if self.animo == self.SORPRENDIDO: return 1.15, 1.32
        if self.animo == self.DORMIDO:     return 1.00, 0.24
        return 1.0, 1.0

    def actualizar(self, dt: float):
        self._t += dt

        # Gesto en curso (se apaga solo al cumplir su duración)
        if self._gesto is not None:
            self._gesto_t += dt
            if self._gesto_t >= self._gesto_dur:
                self._gesto = None

        # Suavizado de la mirada
        k = min(1.0, dt * 9.0)
        self._mxs += (self._mx - self._mxs) * k
        self._mys += (self._my - self._mys) * k

        # Tamaño hacia el objetivo de la emoción
        wt, ht = self._targets()
        ks = min(1.0, dt * 8.0)
        self._wf += (wt - self._wf) * ks
        self._hf += (ht - self._hf) * ks

        # Parpadeo (el DORMIDO ya está "cerrado" por el alto, no parpadea)
        if self.animo == self.DORMIDO:
            self._parp = 0.0
        else:
            self._prox -= dt
            if self._prox <= 0:
                fase = -self._prox
                if fase < 0.07:
                    self._parp = fase / 0.07
                elif fase < 0.14:
                    self._parp = 1.0 - (fase - 0.07) / 0.07
                else:
                    self._parp = 0.0
                    self._prox = self._sortear()
            else:
                self._parp = 0.0

    # ── Dibujo ─────────────────────────────────────────────────────────────────

    def dibujar(self, sup: pygame.Surface, cx: int, cy: int, escala: float = 1.0):
        s = escala

        # Movimiento global según la emoción
        if self.animo == self.ESTRELLADO:
            bob = -abs(math.sin(self._t * 6.0)) * 10      # saltitos de emoción
        elif self.animo == self.TRISTE:
            bob = 8                                        # mirada caída
        elif self.animo == self.HABLANDO:
            bob = math.sin(self._t * 5.0) * 4             # acompaña a la voz
        else:
            bob = math.sin(self._t * 1.6) * 3             # respiración
        cy = int(cy + bob * s)

        # Desplazamiento por la mirada
        dx = (self._mxs - 0.5) * 2.0
        dy = (self._mys - 0.5) * 2.0
        gx = dx * self.rango_x * s
        gy = dy * self.rango_y * s

        w = self.base_w * self._wf * s
        h = self.base_h * self._hf * s * (1.0 - self._parp)

        # ── Gestos: desplazan/achatan los ojos por encima de todo lo anterior ──
        cierre = [0.0, 0.0]        # cierre extra por ojo (0 = abierto)
        if self._gesto:
            p = self._gesto_t / self._gesto_dur      # progreso 0..1
            if self._gesto == "guino":
                # Un solo ojo se cierra y se abre (medio seno)
                idx = 1 if self._gesto_lado > 0 else 0
                cierre[idx] = math.sin(p * math.pi)
            elif self._gesto == "asentir":
                gy += math.sin(p * math.pi * 2) * self.base_h * 0.30 * s
            elif self._gesto == "negar":
                gx += math.sin(p * math.pi * 4) * self.base_w * 0.30 * s
            elif self._gesto == "saltar":
                cy -= int(abs(math.sin(p * math.pi)) * self.base_h * 0.45 * s)
            elif self._gesto == "alrededor":
                gx += math.sin(p * math.pi * 2) * self.rango_x * 1.6 * s

        for i, lado in enumerate((-1, 1)):
            ex = cx + lado * (self.sep * 0.5) * s + gx
            ey = cy + gy
            hh = h * (1.0 - cierre[i])
            self._ojo(sup, lado, ex, ey, w, max(2.0, hh), s)

    def _ojo(self, sup, lado, ex, ey, w, h, s):
        # Ojos de estrella: reemplazan el rectángulo redondeado.
        if self.animo == self.ESTRELLADO:
            pulso = 1.0 + math.sin(self._t * 6.0) * 0.12
            r = w * 0.60 * pulso
            pygame.draw.polygon(sup, self.ESTRELLA,
                                self._estrella(ex, ey, r, r * 0.45))
            return

        # Ojos de corazón: para la invitación a unirse al club.
        if self.animo == self.AMOR:
            pulso = 1.0 + math.sin(self._t * 5.0) * 0.10
            pygame.draw.polygon(sup, self.CORAZON,
                                self._corazon(ex, ey, w * 0.62 * pulso))
            return

        rect = pygame.Rect(0, 0, int(w), int(h))
        rect.center = (int(ex), int(ey))
        pygame.draw.rect(sup, self.OJO, rect, border_radius=int(self.radio * s))

        top, L, R = rect.top, rect.left, rect.right

        if self.animo == self.ENOJADO:
            # Ceño: párpado alto en el borde EXTERIOR, bajo en el INTERIOR.
            hh = h * 0.58
            if lado < 0:   # ojo izquierdo → interior = borde derecho
                pts = [(L, top), (R, top), (R, top + hh)]
            else:          # ojo derecho → interior = borde izquierdo
                pts = [(L, top), (R, top), (L, top + hh)]
            pygame.draw.polygon(sup, self.FONDO, pts)

        elif self.animo == self.TRISTE:
            # Al revés del enojado: párpado alto en el INTERIOR.
            hh = h * 0.5
            if lado < 0:   # interior = derecho
                pts = [(L, top), (R, top), (L, top + hh)]
            else:
                pts = [(L, top), (R, top), (R, top + hh)]
            pygame.draw.polygon(sup, self.FONDO, pts)

        elif self.animo == self.FELIZ:
            # Media luna: tapo la mitad de arriba con una elipse del fondo → ‿
            cover = pygame.Rect(0, 0, int(w * 1.5), int(h * 1.1))
            cover.center = (int(ex), int(ey - h * 0.55))
            pygame.draw.ellipse(sup, self.FONDO, cover)

    # ── Interno ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _estrella(cx, cy, r_ext, r_int, puntas=5):
        pts = []
        for i in range(puntas * 2):
            r = r_ext if i % 2 == 0 else r_int
            ang = math.pi / puntas * i - math.pi / 2
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        return pts

    @staticmethod
    def _corazon(cx, cy, r, pasos=28):
        """Corazón paramétrico, centrado y escalado a un radio `r`."""
        pts = []
        for i in range(pasos):
            t = 2.0 * math.pi * i / pasos
            x = 16 * math.sin(t) ** 3
            y = -(13 * math.cos(t) - 5 * math.cos(2 * t)
                  - 2 * math.cos(3 * t) - math.cos(4 * t))
            pts.append((cx + x * r / 16.0, cy + y * r / 16.0))
        return pts

    @staticmethod
    def _sortear() -> float:
        return random.uniform(2.2, 5.5)


# ─────────────────────────────────────────────────────────────────────────────
# DEMO:  python3 ojos.py           → ventana interactiva (teclas 1-9, mouse mira)
#        python3 ojos.py --lamina out.png   → guarda una lámina con las caras
# ─────────────────────────────────────────────────────────────────────────────

_MOODS = [Ojos.NEUTRO, Ojos.FELIZ, Ojos.ENOJADO, Ojos.SORPRENDIDO,
          Ojos.TRISTE, Ojos.ESTRELLADO, Ojos.DORMIDO, Ojos.AMOR, Ojos.HABLANDO]


def _lamina(path):
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    cols, filas = 5, 2
    cw, ch = 360, 240
    W, H = cols * cw, filas * ch
    sup = pygame.Surface((W, H))
    sup.fill(Ojos.FONDO)
    fuente = pygame.font.SysFont("dejavusans", 22, bold=True)
    for i, m in enumerate(_MOODS):
        cx = (i % cols) * cw + cw // 2
        cy = (i // cols) * ch + ch // 2
        o = Ojos()
        o.ajustar_a(cw, ch - 40)
        o.set_animo(m)
        o.mirar(0.5, 0.45)
        for _ in range(40):
            o.actualizar(1 / 60)
        o.dibujar(sup, cx, cy - 12)
        etiqueta = fuente.render(m.upper(), True, (150, 160, 175))
        sup.blit(etiqueta, etiqueta.get_rect(center=(cx, cy + ch // 2 - 20)))
    pygame.image.save(sup, path)
    print("Lámina guardada en", path)


def _demo():
    pygame.init()
    ANCHO, ALTO = 900, 500
    pantalla = pygame.display.set_mode((ANCHO, ALTO), pygame.RESIZABLE)
    pygame.display.set_caption("Ojos — 1-9 emociones · G guiño · A sí · N no · S salto · ESC")
    fuente = pygame.font.SysFont("dejavusans", 20)
    reloj = pygame.time.Clock()

    ojos = Ojos()
    ojos.ajustar_a(ANCHO, int(ALTO * 0.86))
    idx = 0
    ojos.set_animo(_MOODS[idx])

    corriendo = True
    while corriendo:
        dt = reloj.tick(60) / 1000.0
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                corriendo = False
            elif ev.type == pygame.VIDEORESIZE:
                ANCHO, ALTO = ev.w, ev.h
                pantalla = pygame.display.set_mode((ANCHO, ALTO), pygame.RESIZABLE)
                ojos.ajustar_a(ANCHO, int(ALTO * 0.86))
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    corriendo = False
                elif pygame.K_1 <= ev.key <= pygame.K_9:
                    i = ev.key - pygame.K_1
                    if i < len(_MOODS):
                        idx = i
                        ojos.set_animo(_MOODS[idx])
                elif ev.key == pygame.K_g: ojos.guinar()
                elif ev.key == pygame.K_a: ojos.asentir()
                elif ev.key == pygame.K_n: ojos.negar()
                elif ev.key == pygame.K_s: ojos.saltar()
                elif ev.key == pygame.K_m: ojos.mirar_alrededor()

        mx, my = pygame.mouse.get_pos()
        ojos.mirar(mx / ANCHO, my / ALTO)
        ojos.actualizar(dt)

        pantalla.fill(Ojos.FONDO)
        ojos.dibujar(pantalla, ANCHO // 2, int(ALTO * 0.46))
        ayuda = fuente.render(f"{ojos.animo.upper()}   ·   1-9 emociones · G guiño · A sí · N no · S salto · M alrededor",
                              True, (140, 150, 165))
        pantalla.blit(ayuda, ayuda.get_rect(center=(ANCHO // 2, ALTO - 26)))
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "--lamina":
        _lamina(sys.argv[2])
    else:
        _demo()
