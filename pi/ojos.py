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

Técnica: cada ojo es un rectángulo redondeado. La emoción se logra tapando
parte del ojo con "párpados" del color del fondo (triángulos o una elipse).
Es como se hacen los ojos de robot y se lee clarísimo en pantallas chicas.

    ojos = Ojos()
    ojos.set_animo(Ojos.ENOJADO)
    ojos.mirar(0.8, 0.4)      # 0=izq, 0.5=centro, 1=der (coords de la cámara)
    ojos.actualizar(dt)
    ojos.dibujar(pantalla, cx, cy, escala)

Asume FONDO oscuro (pantalla dedicada a los ojos). El color de fondo es Ojos.FONDO.
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

    # ── Colores ───────────────────────────────────────────────────────────────
    OJO      = (64, 224, 255)     # cian brillante
    FONDO    = (6, 10, 18)        # el fondo de la pantalla de los ojos
    ESTRELLA = (255, 216, 77)     # ojos de estrella

    def __init__(self, ancho=180, alto=180, radio=52, sep=250):
        self.animo   = self.NEUTRO
        self.base_w  = ancho      # ancho base del ojo (px a escala 1.0)
        self.base_h  = alto       # alto base del ojo
        self.radio   = radio      # redondeo de esquinas
        self.sep     = sep        # separación entre centros de los ojos

        self._t = 0.0
        # Mirada (0..1), con suavizado
        self._mx = 0.5; self._my = 0.5
        self._mxs = 0.5; self._mys = 0.5
        # Parpadeo
        self._parp = 0.0
        self._prox = self._sortear()
        # Factores de tamaño animados (para transiciones suaves entre emociones)
        self._wf = 1.0; self._hf = 1.0

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

    # ── Actualización ──────────────────────────────────────────────────────────

    def _targets(self):
        """(ancho, alto) objetivo del ojo según la emoción."""
        if self.animo == self.SORPRENDIDO: return 1.15, 1.32
        if self.animo == self.DORMIDO:     return 1.00, 0.24
        return 1.0, 1.0

    def actualizar(self, dt: float):
        self._t += dt

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
        else:
            bob = math.sin(self._t * 1.6) * 3             # respiración
        cy = int(cy + bob * s)

        # Desplazamiento por la mirada
        dx = (self._mxs - 0.5) * 2.0
        dy = (self._mys - 0.5) * 2.0
        gx = dx * 24 * s
        gy = dy * 15 * s

        w = self.base_w * self._wf * s
        h = self.base_h * self._hf * s * (1.0 - self._parp)

        for lado in (-1, 1):
            ex = cx + lado * (self.sep * 0.5) * s + gx
            ey = cy + gy
            self._ojo(sup, lado, ex, ey, w, max(2.0, h), s)

    def _ojo(self, sup, lado, ex, ey, w, h, s):
        # Ojos de estrella: reemplazan el rectángulo redondeado.
        if self.animo == self.ESTRELLADO:
            pulso = 1.0 + math.sin(self._t * 6.0) * 0.12
            r = w * 0.60 * pulso
            pygame.draw.polygon(sup, self.ESTRELLA,
                                self._estrella(ex, ey, r, r * 0.45))
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
    def _sortear() -> float:
        return random.uniform(2.2, 5.5)


# ─────────────────────────────────────────────────────────────────────────────
# DEMO:  python3 ojos.py           → ventana interactiva (teclas 1-7, mouse mira)
#        python3 ojos.py --lamina out.png   → guarda una lámina con las 7 caras
# ─────────────────────────────────────────────────────────────────────────────

_MOODS = [Ojos.NEUTRO, Ojos.FELIZ, Ojos.ENOJADO, Ojos.SORPRENDIDO,
          Ojos.TRISTE, Ojos.ESTRELLADO, Ojos.DORMIDO]


def _lamina(path):
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    cols, filas = 4, 2
    cw, ch = 360, 240
    W, H = cols * cw, filas * ch
    sup = pygame.Surface((W, H))
    sup.fill(Ojos.FONDO)
    fuente = pygame.font.SysFont("dejavusans", 22, bold=True)
    for i, m in enumerate(_MOODS):
        cx = (i % cols) * cw + cw // 2
        cy = (i // cols) * ch + ch // 2
        o = Ojos(sep=190)
        o.set_animo(m)
        o.mirar(0.5, 0.45)
        for _ in range(40):
            o.actualizar(1 / 60)
        o.dibujar(sup, cx, cy - 12, escala=0.62)
        etiqueta = fuente.render(m.upper(), True, (150, 160, 175))
        sup.blit(etiqueta, etiqueta.get_rect(center=(cx, cy + ch // 2 - 20)))
    pygame.image.save(sup, path)
    print("Lámina guardada en", path)


def _demo():
    pygame.init()
    ANCHO, ALTO = 900, 500
    pantalla = pygame.display.set_mode((ANCHO, ALTO))
    pygame.display.set_caption("Ojos — demo (1-7 emociones · mouse mira · ESC)")
    fuente = pygame.font.SysFont("dejavusans", 20)
    reloj = pygame.time.Clock()

    ojos = Ojos()
    idx = 0
    ojos.set_animo(_MOODS[idx])

    corriendo = True
    while corriendo:
        dt = reloj.tick(60) / 1000.0
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                corriendo = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    corriendo = False
                elif pygame.K_1 <= ev.key <= pygame.K_7:
                    idx = ev.key - pygame.K_1
                    ojos.set_animo(_MOODS[idx])

        mx, my = pygame.mouse.get_pos()
        ojos.mirar(mx / ANCHO, my / ALTO)
        ojos.actualizar(dt)

        pantalla.fill(Ojos.FONDO)
        ojos.dibujar(pantalla, ANCHO // 2, ALTO // 2 - 10, escala=1.0)
        ayuda = fuente.render(f"{ojos.animo.upper()}   ·   1-7 emociones · mouse mira · ESC",
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
