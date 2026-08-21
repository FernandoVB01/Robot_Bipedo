#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — QR de sesión en la pantalla
=============================================================================
Rol : Dibuja en la pantalla del robot el código QR que el cliente escanea con
      su celular para abrir la WebApp.

      Esto INVIERTE el flujo anterior: antes el QR era un papel que el cliente
      le mostraba a la cámara; ahora la pantalla lo genera y el celular lo lee.

Dependencia:
      pip install qrcode        ← sin el [pil]: no hace falta Pillow

      Se usa `qrcode` solo para calcular la matriz de módulos
      (`QRCode.get_matrix()`, que es Python puro) y se dibuja con rectángulos
      de Pygame. Así el robot no arrastra Pillow a la Raspberry para pintar
      cuadraditos negros.

      Si la librería no está instalada, el robot NO se cae: muestra la URL en
      texto grande para que el cliente la escriba a mano. Misma filosofía de
      degradación que el resto del proyecto.

─────────────────────────────────────────────────────────────────────────────
DOS COSAS QUE DECIDEN SI EL QR SE ESCANEA O NO
─────────────────────────────────────────────────────────────────────────────
· NEGRO SOBRE BLANCO, siempre. La GUI es oscura y tienta pintarlo con la
  paleta del pingüino, pero los lectores de los celulares se vuelven lentos o
  fallan con poco contraste. El QR va sobre una tarjeta blanca, y punto.

· ZONA DE SILENCIO (el margen blanco). La norma pide 4 módulos. Sin ese margen
  muchos lectores no encuentran el código, sobre todo si detrás hay un fondo
  con textura.
=============================================================================
"""

from typing import Optional, Tuple

import pygame

try:
    import qrcode
    QR_DISPONIBLE = True
except ImportError:
    QR_DISPONIBLE = False
    print("[QR] La librería 'qrcode' no está instalada — se mostrará la URL en "
          "texto. Para el código: pip install qrcode")


# Módulos de margen blanco alrededor del código (la norma pide 4).
ZONA_SILENCIO = 4


class GeneradorQR:
    """
    Convierte una URL en una superficie de Pygame lista para pegar en pantalla.

    Guarda en caché el último resultado: regenerar el QR en cada cuadro sería
    tirar CPU de la Raspberry a la basura para dibujar exactamente lo mismo
    treinta veces por segundo.
    """

    def __init__(self):
        self._cache: dict[Tuple[str, int], pygame.Surface] = {}

    @property
    def disponible(self) -> bool:
        return QR_DISPONIBLE

    def superficie(self, url: str, lado_px: int) -> Optional[pygame.Surface]:
        """
        Devuelve una superficie cuadrada de `lado_px` con el QR de `url`,
        o None si la librería no está instalada.
        """
        if not QR_DISPONIBLE or not url:
            return None

        clave = (url, lado_px)
        if clave in self._cache:
            return self._cache[clave]

        # ERROR_CORRECT_M (15% de recuperación) es el punto justo para una
        # pantalla: aguanta reflejos y fotos en ángulo sin agrandar el código.
        qr = qrcode.QRCode(
            version=None,                                   # el más chico que entre
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=0,                                       # el margen lo ponemos acá
        )
        qr.add_data(url)
        qr.make(fit=True)
        matriz = qr.get_matrix()

        n = len(matriz)
        total_modulos = n + 2 * ZONA_SILENCIO

        # El lado del módulo se redondea hacia abajo a un entero de píxeles.
        # Si se dibujara con decimales, unos módulos saldrían de 3 px y otros de
        # 4 y el lector se confunde: los módulos tienen que ser todos iguales.
        lado_modulo = max(1, lado_px // total_modulos)
        lado_real = lado_modulo * total_modulos

        sup = pygame.Surface((lado_real, lado_real))
        sup.fill((255, 255, 255))

        negro = (0, 0, 0)
        off = ZONA_SILENCIO * lado_modulo
        for fila in range(n):
            for col in range(n):
                if matriz[fila][col]:
                    pygame.draw.rect(
                        sup, negro,
                        (off + col * lado_modulo, off + fila * lado_modulo,
                         lado_modulo, lado_modulo))

        self._cache.clear()          # solo interesa el QR vigente
        self._cache[clave] = sup
        return sup

    def dibujar(self, destino: pygame.Surface, url: str,
                centro: Tuple[int, int], lado_px: int,
                fuente: Optional[pygame.font.Font] = None) -> bool:
        """
        Pega el QR centrado en `centro`. Si no hay librería, escribe la URL.
        Devuelve True si dibujó el código, False si cayó al texto.
        """
        sup = self.superficie(url, lado_px)

        if sup is not None:
            rect = sup.get_rect(center=centro)
            # Marco blanco extra: despega el código del fondo oscuro de la GUI.
            pygame.draw.rect(destino, (255, 255, 255), rect.inflate(16, 16),
                             border_radius=8)
            destino.blit(sup, rect)
            return True

        # ── Respaldo sin librería: la URL en texto ──────────────────────────
        if fuente is None:
            return False
        texto = (url or "").replace("http://", "")
        img = fuente.render(texto, True, (255, 255, 255))
        destino.blit(img, img.get_rect(center=centro))
        aviso = fuente.render("Escribí esta dirección en tu navegador",
                              True, (160, 155, 195))
        destino.blit(aviso, aviso.get_rect(
            center=(centro[0], centro[1] + img.get_height() + 8)))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# PRUEBA RÁPIDA:  python3 pi_qr.py
# Abre una ventana con un QR de ejemplo. Escaneálo con el celular para
# comprobar que se lee bien al tamaño del panel.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.43.50:8000/c/AbCdEf12"

    pygame.init()
    pantalla = pygame.display.set_mode((480, 320))
    pygame.display.set_caption("QR de sesión — prueba")
    fuente = pygame.font.SysFont("dejavusans", 16)

    gen = GeneradorQR()
    print(f"Librería qrcode disponible: {gen.disponible}")
    print(f"URL: {url}")

    reloj = pygame.time.Clock()
    corriendo = True
    while corriendo:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (
                    ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
                corriendo = False

        pantalla.fill((10, 8, 28))
        gen.dibujar(pantalla, url, (240, 150), 200, fuente)
        pie = fuente.render("ESC para salir", True, (160, 155, 195))
        pantalla.blit(pie, pie.get_rect(center=(240, 300)))
        pygame.display.flip()
        reloj.tick(30)

    pygame.quit()
