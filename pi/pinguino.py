#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — La cara del robot
=============================================================================
Rol : Pingüi, el personaje que vive en la pantalla. Parpadea, mira a los lados,
      sigue a la persona con los ojos, cambia de humor según lo que esté
      pasando y baila.

      Es lo que convierte una pantalla con texto en algo con lo que la gente
      quiere interactuar. Un cliente no se acerca a un monitor; se acerca a algo
      que lo mira.

Dibujado con primitivas de Pygame — nada de imágenes. Motivos:
  · No hay archivos que copiar a la Raspberry ni que se puedan perder.
  · Escala sin pixelarse: el mismo código se ve bien en el HDMI de 1280x720 de
    las pruebas y en el panel SPI de 480x320 del robot.
  · Animar es mover números, no cargar veinte PNG en memoria.

Todo el dibujo sale de `escala`, así que la cara entera se agranda o achica con
un solo número.

─────────────────────────────────────────────────────────────────────────────
DETALLE DE ANIMACIÓN: NO SE USA ROTACIÓN
─────────────────────────────────────────────────────────────────────────────
Rotar en Pygame obliga a redibujar a una superficie aparte y girarla en cada
cuadro. En la Raspberry, con el panel por SPI, eso se nota. El "inclinarse" del
baile se hace desplazando las piezas y estirándolas un poco (squash & stretch),
que es lo que hacen los dibujos animados y sale mucho más barato.

Uso:
    ping = Pinguino()
    ping.set_animo(Pinguino.FELIZ)
    ping.mirar(0.8)                # 0=izquierda, 0.5=centro, 1=derecha
    ...
    ping.actualizar(dt)
    ping.dibujar(pantalla, cx=640, cy=360, escala=1.0)
=============================================================================
"""

import math
import random

import pygame


class Pinguino:
    """Pingüi. Una cara, seis humores y muchas ganas de bailar."""

    # ── Humores ──────────────────────────────────────────────────────────────
    DORMIDO     = "dormido"      # en reposo, esperando que aparezca alguien
    ATENTO      = "atento"       # vio a alguien y lo está mirando
    FELIZ       = "feliz"        # saludo / bienvenida
    SORPRENDIDO = "sorprendido"  # reacción al detectar a alguien
    HABLANDO    = "hablando"     # explicando algo en pantalla
    BAILANDO    = "bailando"     # la rutina de baile
    ESTRELLADO  = "estrellado"   # ojos de estrella al detectar a alguien

    # ── Colores ──────────────────────────────────────────────────────────────
    CUERPO   = (28, 26, 48)
    CUERPO_L = (52, 48, 84)      # brillo del cuerpo
    PANZA    = (245, 245, 252)
    OJO      = (255, 255, 255)
    PUPILA   = (16, 14, 30)
    PICO     = (255, 168, 40)
    PICO_OSC = (214, 128, 20)
    PATA     = (255, 150, 30)
    RUBOR    = (255, 130, 140)
    BUFANDA  = (255, 95, 50)
    ESTRELLA = (255, 216, 77)    # ojos de estrella

    def __init__(self):
        self.animo = self.DORMIDO
        self._t = 0.0                  # reloj propio, en segundos

        # Mirada: 0.0 = todo a la izquierda, 0.5 = centro, 1.0 = derecha
        self._mira_x = 0.5
        self._mira_y = 0.5
        self._mira_x_suave = 0.5       # se persigue con suavizado
        self._mira_y_suave = 0.5

        # Parpadeo
        self._parpadeo = 0.0           # 0 = ojo abierto, 1 = ojo cerrado
        self._prox_parpadeo = self._sortear_parpadeo()

        # Baile
        self._baile_t = 0.0

    # ── API ──────────────────────────────────────────────────────────────────

    def set_animo(self, animo: str):
        if animo != self.animo:
            self.animo = animo
            if animo == self.BAILANDO:
                self._baile_t = 0.0

    def mirar(self, x: float, y: float = 0.5):
        """
        Hacia dónde mira. Coordenadas normalizadas del encuadre de la cámara,
        que es lo que devuelve la PC para la persona detectada.
        """
        self._mira_x = max(0.0, min(1.0, float(x)))
        self._mira_y = max(0.0, min(1.0, float(y)))

    def mirar_al_frente(self):
        self._mira_x = 0.5
        self._mira_y = 0.5

    def actualizar(self, dt: float):
        """Avanza las animaciones. `dt` en segundos."""
        self._t += dt

        # Suavizado de la mirada: los ojos no saltan de golpe al nuevo punto,
        # lo persiguen. Sin esto, cada temblor del detector es un tic nervioso.
        k = min(1.0, dt * 9.0)
        self._mira_x_suave += (self._mira_x - self._mira_x_suave) * k
        self._mira_y_suave += (self._mira_y - self._mira_y_suave) * k

        # Parpadeo
        if self.animo == self.DORMIDO:
            self._parpadeo = 1.0
        else:
            self._prox_parpadeo -= dt
            if self._prox_parpadeo <= 0:
                # Un parpadeo dura ~0.16 s: 0.08 de cierre y 0.08 de apertura.
                fase = -self._prox_parpadeo
                if fase < 0.08:
                    self._parpadeo = fase / 0.08
                elif fase < 0.16:
                    self._parpadeo = 1.0 - (fase - 0.08) / 0.08
                else:
                    self._parpadeo = 0.0
                    self._prox_parpadeo = self._sortear_parpadeo()
            else:
                self._parpadeo = 0.0

        if self.animo == self.BAILANDO:
            self._baile_t += dt

    # ── Dibujo ───────────────────────────────────────────────────────────────

    def dibujar(self, sup: pygame.Surface, cx: int, cy: int, escala: float = 1.0):
        """
        Dibuja a Pingüi centrado en (cx, cy). `escala` 1.0 ≈ 400 px de alto.

        El orden de dibujo importa (lo de atrás primero):
            patas → aletas → cuerpo → panza → cabeza → bufanda → cara
        Las aletas van ANTES del cuerpo para que queden pegadas a los costados,
        y la bufanda DESPUÉS de la cabeza para que tape la unión cabeza-cuerpo
        pero por debajo del pico.
        """
        s = escala
        E = lambda v: int(v * s)          # noqa: E731 — atajo de escala

        # ── Movimiento global ────────────────────────────────────────────
        if self.animo == self.BAILANDO:
            # Rebote rápido + balanceo lateral: el "baile de Club Penguin".
            bob    = math.sin(self._baile_t * 9.0) * 14
            lean   = math.sin(self._baile_t * 4.5) * 26
            squash = 1.0 + math.sin(self._baile_t * 9.0) * 0.07
        elif self.animo == self.DORMIDO:
            # Respiración lenta.
            bob    = math.sin(self._t * 1.2) * 4
            lean   = 0.0
            squash = 1.0 + math.sin(self._t * 1.2) * 0.02
        elif self.animo == self.ESTRELLADO:
            # Saltitos de emoción al ver a alguien.
            bob    = -abs(math.sin(self._t * 6.0)) * 12
            lean   = 0.0
            squash = 1.0 + math.sin(self._t * 6.0) * 0.05
        else:
            bob    = math.sin(self._t * 2.2) * 6
            lean   = 0.0
            squash = 1.0

        cx = int(cx + lean * s)
        cy = int(cy + bob * s)

        # ── Anatomía ─────────────────────────────────────────────────────
        # La cabeza se balancea un poco más que el cuerpo: da sensación de peso.
        cabeza_cx = int(cx + lean * s * 0.35)
        cabeza_cy = cy - E(100)
        cabeza_r  = E(88)

        cuerpo_cy = cy + E(75)
        cuerpo_w  = E(260)
        cuerpo_h  = int(E(230) * squash)
        cuerpo_abajo = cuerpo_cy + cuerpo_h // 2

        # ── Patas (lo más atrás) ─────────────────────────────────────────
        sep_pata = E(46)
        if self.animo == self.BAILANDO:
            paso = math.sin(self._baile_t * 9.0) * E(12)   # zapateo alternado
        else:
            paso = 0
        for lado, dy in ((-1, paso), (1, -paso)):
            pata = pygame.Rect(0, 0, E(56), E(24))
            pata.center = (cx + lado * sep_pata, int(cuerpo_abajo - E(4) + dy))
            pygame.draw.ellipse(sup, self.PATA, pata)

        # ── Aletas ───────────────────────────────────────────────────────
        # Van a los costados y SOBRESALEN del cuerpo: si quedan por dentro, el
        # cuerpo las tapa y el pingüino parece un huevo sin brazos.
        if self.animo == self.BAILANDO:
            aleta = math.sin(self._baile_t * 9.0 + math.pi / 2) * E(34)
        elif self.animo in (self.FELIZ, self.ESTRELLADO):
            aleta = math.sin(self._t * 7.0) * E(16)
        else:
            aleta = math.sin(self._t * 2.0) * E(5)
        for lado, dy in ((-1, aleta), (1, -aleta)):
            ala = pygame.Rect(0, 0, E(52), E(120))
            ala.center = (cx + lado * E(140), int(cuerpo_cy - E(10) + dy))
            pygame.draw.ellipse(sup, self.CUERPO, ala)

        # ── Cuerpo ───────────────────────────────────────────────────────
        cuerpo = pygame.Rect(0, 0, cuerpo_w, cuerpo_h)
        cuerpo.center = (cx, cuerpo_cy)
        pygame.draw.ellipse(sup, self.CUERPO, cuerpo)

        # ── Panza ────────────────────────────────────────────────────────
        # Más chica que el cuerpo, para que quede un borde oscuro alrededor.
        panza = pygame.Rect(0, 0, E(186), int(E(190) * squash))
        panza.center = (cx, cuerpo_cy + E(12))
        pygame.draw.ellipse(sup, self.PANZA, panza)

        # ── Cabeza ───────────────────────────────────────────────────────
        pygame.draw.circle(sup, self.CUERPO, (cabeza_cx, cabeza_cy), cabeza_r)
        # Brillo suave arriba a la izquierda: da volumen. Chico y DENTRO de la
        # cabeza — si es grande se lee como una mancha, no como un reflejo.
        brillo = pygame.Rect(0, 0, E(54), E(40))
        brillo.center = (cabeza_cx - E(40), cabeza_cy - E(52))
        pygame.draw.ellipse(sup, self.CUERPO_L, brillo)

        # ── Bufanda ──────────────────────────────────────────────────────
        # A la altura del cuello: tapa la unión cabeza-cuerpo. Va por debajo de
        # la cara, así que se dibuja antes que ojos y pico.
        bufanda = pygame.Rect(0, 0, E(196), E(38))
        bufanda.center = (cx, cy - E(16))
        pygame.draw.ellipse(sup, self.BUFANDA, bufanda)
        punta = pygame.Rect(0, 0, E(36), E(70))
        punta.center = (cx + E(78), cy + E(14) + int(aleta * 0.25))
        pygame.draw.ellipse(sup, self.BUFANDA, punta)

        # ── Ojos ─────────────────────────────────────────────────────────
        sep_ojo = E(42)
        y_ojo   = cabeza_cy - E(14)
        r_ojo_x, r_ojo_y = E(30), E(34)
        cx_cara = cabeza_cx

        # Desplazamiento de las pupilas según hacia dónde mira.
        # (mira_x 0..1) → (-1..1), y se limita para que no se salgan del ojo.
        dx = (self._mira_x_suave - 0.5) * 2.0
        dy = (self._mira_y_suave - 0.5) * 2.0
        px = dx * E(11)
        py = dy * E(8)

        for lado in (-1, 1):
            ox = cx_cara + lado * sep_ojo

            if self.animo == self.ESTRELLADO:
                # Ojos de estrella: laten para que "brillen". Siguen la mirada.
                pulso = 1.0 + math.sin(self._t * 6.0) * 0.12
                r_ext = E(32) * pulso
                pts = self._puntos_estrella(int(ox + px), int(y_ojo + py),
                                            r_ext, r_ext * 0.45)
                pygame.draw.polygon(sup, self.ESTRELLA, pts)
                continue

            if self.animo == self.DORMIDO:
                # Ojos cerrados: dos arcos. Más simpático que dos rayas.
                pygame.draw.arc(sup, self.PUPILA,
                                (ox - r_ojo_x, y_ojo - r_ojo_y // 2,
                                 r_ojo_x * 2, r_ojo_y),
                                math.pi, 2 * math.pi, max(2, E(5)))
                continue

            # Blanco del ojo, achatado por el parpadeo
            alto_ojo = max(2, int(r_ojo_y * 2 * (1.0 - self._parpadeo)))
            ojo = pygame.Rect(0, 0, r_ojo_x * 2, alto_ojo)
            ojo.center = (ox, y_ojo)
            pygame.draw.ellipse(sup, self.OJO, ojo)

            if self._parpadeo < 0.6:
                r_pup = E(15) if self.animo != self.SORPRENDIDO else E(19)
                pygame.draw.circle(sup, self.PUPILA,
                                   (int(ox + px), int(y_ojo + py)), r_pup)
                # Reflejo: el detalle que hace que el ojo parezca vivo.
                pygame.draw.circle(sup, self.OJO,
                                   (int(ox + px + r_pup * 0.35),
                                    int(y_ojo + py - r_pup * 0.35)),
                                   max(2, E(5)))

        # ── Rubor (cuando está contento) ─────────────────────────────────
        if self.animo in (self.FELIZ, self.BAILANDO, self.ESTRELLADO):
            for lado in (-1, 1):
                rub = pygame.Rect(0, 0, E(32), E(17))
                rub.center = (cx_cara + lado * E(72), y_ojo + E(36))
                # Superficie aparte para poder darle transparencia.
                capa = pygame.Surface(rub.size, pygame.SRCALPHA)
                pygame.draw.ellipse(capa, (*self.RUBOR, 130), capa.get_rect())
                sup.blit(capa, rub)

        # ── Pico ─────────────────────────────────────────────────────────
        # Va sobre la cabeza, bien por encima de la bufanda. Si se solapan, el
        # naranja del pico y el de la bufanda se funden en un borrón y el
        # pingüino parece tener una boca gigante.
        y_pico = y_ojo + E(40)
        if self.animo == self.HABLANDO:
            abertura = (math.sin(self._t * 11.0) * 0.5 + 0.5) * E(14)
        elif self.animo == self.BAILANDO:
            abertura = E(12)
        else:
            abertura = 0

        pico_sup = [
            (cx_cara - E(24), y_pico),
            (cx_cara + E(24), y_pico),
            (cx_cara, y_pico + E(20)),
        ]
        pygame.draw.polygon(sup, self.PICO, pico_sup)
        if abertura > 0:
            pico_inf = [
                (cx_cara - E(18), int(y_pico + abertura)),
                (cx_cara + E(18), int(y_pico + abertura)),
                (cx_cara, int(y_pico + abertura + E(15))),
            ]
            pygame.draw.polygon(sup, self.PICO_OSC, pico_inf)

        # ── Zzz cuando duerme ────────────────────────────────────────────
        if self.animo == self.DORMIDO:
            self._dibujar_zzz(sup, cabeza_cx + E(96), cabeza_cy - E(70), s)

    # ── Interno ──────────────────────────────────────────────────────────────

    def _dibujar_zzz(self, sup, x, y, s):
        """Tres 'z' que suben y se desvanecen."""
        fuente = self._fuente_zzz(int(30 * s))
        if fuente is None:
            return
        for i in range(3):
            fase = (self._t * 0.45 + i * 0.33) % 1.0
            alpha = int(200 * (1.0 - fase))
            if alpha <= 0:
                continue
            img = fuente.render("z", True, (255, 255, 255))
            img.set_alpha(alpha)
            sup.blit(img, (int(x + fase * 26 * s),
                           int(y - fase * 70 * s)))

    _fuente_cache: dict = {}

    @classmethod
    def _fuente_zzz(cls, tam):
        if tam not in cls._fuente_cache:
            try:
                cls._fuente_cache[tam] = pygame.font.SysFont("dejavusans", tam,
                                                             bold=True)
            except Exception:
                cls._fuente_cache[tam] = None
        return cls._fuente_cache[tam]

    @staticmethod
    def _puntos_estrella(cx, cy, r_ext, r_int, puntas=5):
        """Vértices de una estrella de N puntas centrada en (cx, cy), punta arriba."""
        pts = []
        for i in range(puntas * 2):
            r = r_ext if i % 2 == 0 else r_int
            ang = math.pi / puntas * i - math.pi / 2
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        return pts

    @staticmethod
    def _sortear_parpadeo() -> float:
        """Cada cuánto parpadea. Al azar, porque un parpadeo con metrónomo se
        ve mecánico y arruina el efecto."""
        return random.uniform(2.2, 5.5)


# ─────────────────────────────────────────────────────────────────────────────
# DEMO:  python3 pinguino.py
# Recorre los seis humores. Teclas 1-6 para saltar; el ratón mueve la mirada.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pygame.init()
    ANCHO, ALTO = 900, 600
    pantalla = pygame.display.set_mode((ANCHO, ALTO))
    pygame.display.set_caption("Pingüi — demo de humores")
    fuente = pygame.font.SysFont("dejavusans", 22)
    reloj = pygame.time.Clock()

    ping = Pinguino()
    humores = [Pinguino.DORMIDO, Pinguino.ATENTO, Pinguino.SORPRENDIDO,
               Pinguino.FELIZ, Pinguino.HABLANDO, Pinguino.BAILANDO,
               Pinguino.ESTRELLADO]
    idx = 0
    ping.set_animo(humores[idx])
    auto = True
    t_cambio = 0.0

    corriendo = True
    while corriendo:
        dt = reloj.tick(60) / 1000.0
        t_cambio += dt

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                corriendo = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    corriendo = False
                elif pygame.K_1 <= ev.key <= pygame.K_7:
                    idx = ev.key - pygame.K_1
                    ping.set_animo(humores[idx])
                    auto = False
                elif ev.key == pygame.K_SPACE:
                    auto = not auto

        if auto and t_cambio > 3.5:
            t_cambio = 0.0
            idx = (idx + 1) % len(humores)
            ping.set_animo(humores[idx])

        # La mirada sigue al ratón
        mx, my = pygame.mouse.get_pos()
        ping.mirar(mx / ANCHO, my / ALTO)
        ping.actualizar(dt)

        pantalla.fill((10, 8, 28))
        ping.dibujar(pantalla, ANCHO // 2, ALTO // 2 - 30, escala=1.0)

        ayuda = fuente.render(
            f"{ping.animo.upper()}   ·   1-6 humor  ·  ESPACIO auto  ·  ESC salir",
            True, (160, 155, 195))
        pantalla.blit(ayuda, ayuda.get_rect(center=(ANCHO // 2, ALTO - 28)))
        pygame.display.flip()

    pygame.quit()
