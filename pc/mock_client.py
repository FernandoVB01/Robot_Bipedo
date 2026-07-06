"""
=============================================================================
ROBOT BÍPEDO — Cliente Simulado / Mock (prueba sin Raspberry Pi)
=============================================================================
Simula el flujo completo del robot:
  1. Envía frames con fondo plano (modo HAND) → servidor debería responder False
  2. Genera una imagen con texto "MANO" como placeholder visual → HAND
  3. Genera un frame con un QR code embebido → QR detectado
  4. Muestra los resultados en consola

NO requiere cámara real. Genera los frames programáticamente con OpenCV.

Cómo ejecutar:
    # En una terminal: arrancar el servidor
    python pc_server.py

    # En otra terminal: correr este mock
    python mock_client.py [IP_PC]  # default: 127.0.0.1 (mismo equipo)

Dependencias:
    pip install pyzmq opencv-python numpy qrcode pillow
=============================================================================
"""

import sys
import time
import json
import cv2
import zmq
import numpy as np

# ── Intento de importar qrcode para generar QRs de prueba ─────────────────
try:
    import qrcode
    from PIL import Image as PILImage
    QR_GEN_AVAILABLE = True
except ImportError:
    QR_GEN_AVAILABLE = False
    print("[MOCK] WARN: 'qrcode' no instalado → los frames QR serán placeholders.")
    print("       Para instalar: pip install qrcode pillow")

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
DEFAULT_SERVER_IP = "127.0.0.1"
ZMQ_PORT          = 5555
FRAME_W, FRAME_H  = 320, 240
JPEG_QUALITY      = 60

# Código QR de prueba (debe coincidir con un producto del config.json)
TEST_QR_PAYLOAD = "PROD:Gaseosa 500ml|DESC:0.20|BASE:1.50"
TEST_CEDULA     = "1713175071"    # Cédula de prueba válida


# ─────────────────────────────────────────────────────────────────────────────
# GENERADORES DE FRAMES
# ─────────────────────────────────────────────────────────────────────────────

def _frame_base() -> np.ndarray:
    """Frame negro con resolución estándar."""
    return np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)


def make_plain_frame(color=(30, 30, 30)) -> np.ndarray:
    """Frame liso de fondo — ninguna mano ni QR visible."""
    frame = np.full((FRAME_H, FRAME_W, 3), color, dtype=np.uint8)
    cv2.putText(frame, "Sin mano / QR", (50, FRAME_H // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 80, 80), 1)
    return frame


def make_hand_placeholder_frame() -> np.ndarray:
    """
    Frame que simula una mano con dedos extendidos.
    Dibuja 5 rectángulos verticales (dedos) sobre una palma rectangular.
    MediaPipe no detectará una mano real aquí, pero sirve para verificar
    que el servidor responde correctamente cuando NO hay mano.
    Para una prueba real de detección, captura una foto tuya con la palma.
    """
    frame = np.full((FRAME_H, FRAME_W, 3), (20, 20, 20), dtype=np.uint8)
    # Palma
    cv2.rectangle(frame, (100, 140), (220, 200), (180, 140, 100), -1)
    # Dedos
    finger_x = [110, 135, 155, 175, 200]
    for x in finger_x:
        cv2.rectangle(frame, (x, 80), (x + 18, 145), (180, 140, 100), -1)
    cv2.putText(frame, "PALMA (placeholder)", (30, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 180), 1)
    return frame


def make_qr_frame(payload: str = TEST_QR_PAYLOAD) -> np.ndarray:
    """
    Genera un frame con el QR code embebido usando la librería 'qrcode'.
    Si no está instalada, devuelve un frame de placeholder.
    """
    if not QR_GEN_AVAILABLE:
        frame = _frame_base()
        cv2.putText(frame, "QR placeholder", (60, FRAME_H // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 200), 1)
        cv2.putText(frame, "(instalar: pip install qrcode pillow)",
                    (10, FRAME_H // 2 + 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (100, 100, 100), 1)
        return frame

    # Generar imagen QR con PIL
    qr = qrcode.QRCode(version=1, box_size=4, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    pil_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # Redimensionar al tamaño del frame y centrar
    qr_w, qr_h = pil_img.size
    scale = min((FRAME_W - 20) / qr_w, (FRAME_H - 20) / qr_h)
    new_w, new_h = int(qr_w * scale), int(qr_h * scale)
    pil_img = pil_img.resize((new_w, new_h), PILImage.LANCZOS)

    frame = np.full((FRAME_H, FRAME_W, 3), 255, dtype=np.uint8)   # Fondo blanco
    x_off = (FRAME_W - new_w) // 2
    y_off = (FRAME_H - new_h) // 2
    frame[y_off:y_off+new_h, x_off:x_off+new_w] = np.array(pil_img)

    return frame


def compress_frame(frame: np.ndarray) -> bytes:
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
    _, buf = cv2.imencode(".jpg", frame, encode_param)
    return buf.tobytes()


# ─────────────────────────────────────────────────────────────────────────────
# CLASE MOCK CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class MockClient:
    def __init__(self, server_ip: str = DEFAULT_SERVER_IP, port: int = ZMQ_PORT):
        self._addr = f"tcp://{server_ip}:{port}"
        self._ctx  = zmq.Context()
        self._sock = self._ctx.socket(zmq.REQ)
        self._sock.setsockopt(zmq.RCVTIMEO, 5000)   # 5 s timeout
        self._sock.setsockopt(zmq.SNDTIMEO, 5000)
        self._sock.setsockopt(zmq.LINGER,   0)

    def connect(self):
        self._sock.connect(self._addr)
        print(f"[MOCK] Conectado a {self._addr}")

    def send(self, mode: str, frame: np.ndarray,
             cedula: str | None = None) -> dict | None:
        """Envía un frame al servidor y devuelve la respuesta JSON."""
        frame_bytes = compress_frame(frame)
        parts = [mode.encode("utf-8"), frame_bytes]
        if cedula:
            parts.append(cedula.encode("utf-8"))
        try:
            self._sock.send_multipart(parts)
            raw = self._sock.recv()
            return json.loads(raw.decode("utf-8"))
        except zmq.Again:
            print("[MOCK] ERROR: timeout — ¿está corriendo pc_server.py?")
            return None
        except Exception as e:
            print(f"[MOCK] ERROR: {e}")
            return None

    def close(self):
        self._sock.close()
        self._ctx.term()


# ─────────────────────────────────────────────────────────────────────────────
# SECUENCIA DE PRUEBA COMPLETA
# ─────────────────────────────────────────────────────────────────────────────

def run_test(server_ip: str):
    client = MockClient(server_ip)
    client.connect()

    sep = "─" * 60
    print(f"\n{sep}")
    print("   ROBOT BÍPEDO — Secuencia de prueba completa")
    print(f"{sep}\n")

    # ── TEST 1: Frame plano → sin mano ────────────────────────────────────
    print("▶  TEST 1 — Frame plano (modo HAND) → esperado: hand_detected=False")
    frame  = make_plain_frame()
    result = client.send("HAND", frame)
    _print_result(result)
    _assert(result, "hand_detected", False, "TEST 1")

    # ── TEST 2: Placeholder de palma → sin mano (MediaPipe no lo detecta) ─
    print("\n▶  TEST 2 — Placeholder palma (modo HAND) → esperado: hand_detected=False")
    print("   (Para True real, usa una foto tuya con la palma abierta)")
    frame  = make_hand_placeholder_frame()
    result = client.send("HAND", frame)
    _print_result(result)

    # ── TEST 3: Frame con QR embebido ─────────────────────────────────────
    print(f"\n▶  TEST 3 — Frame con QR (modo QR) payload: '{TEST_QR_PAYLOAD}'")
    print(f"   Cédula activa: {TEST_CEDULA}")
    frame  = make_qr_frame(TEST_QR_PAYLOAD)
    result = client.send("QR", frame, cedula=TEST_CEDULA)
    _print_result(result)
    if QR_GEN_AVAILABLE:
        _assert(result, "qr_data", TEST_QR_PAYLOAD, "TEST 3")

    # ── TEST 4: Modo BOTH ─────────────────────────────────────────────────
    print("\n▶  TEST 4 — Frame plano (modo BOTH) → esperado: ambos False/None")
    frame  = make_plain_frame()
    result = client.send("BOTH", frame)
    _print_result(result)

    # ── TEST 5: Stress — 30 frames rápidos ───────────────────────────────
    print("\n▶  TEST 5 — Stress: 30 frames consecutivos en modo HAND")
    t0      = time.time()
    errores = 0
    for i in range(30):
        f = make_plain_frame((20 + i, 20, 20))
        r = client.send("HAND", f)
        if r is None:
            errores += 1
    elapsed = time.time() - t0
    fps     = 30 / elapsed
    print(f"   30 frames en {elapsed:.2f} s → {fps:.1f} fps efectivos | errores: {errores}")

    print(f"\n{sep}")
    print("   Prueba completada.")
    print(f"{sep}\n")
    client.close()


def _print_result(result: dict | None):
    if result is None:
        print("   ❌  Sin respuesta del servidor.")
        return
    hand = result.get("hand_detected", "?")
    qr   = result.get("qr_data")
    err  = result.get("error")
    icon_h = "🖐️ " if hand else "  "
    icon_q = "📷 " if qr else "  "
    print(f"   {icon_h} hand_detected = {hand}")
    print(f"   {icon_q} qr_data       = {qr!r}")
    if err:
        print(f"   ⚠️  error         = {err}")


def _assert(result: dict | None, key: str, expected, test_name: str):
    if result is None:
        print(f"   ⚠️  {test_name}: sin respuesta, no se puede verificar.")
        return
    actual = result.get(key)
    if actual == expected:
        print(f"   ✅  {test_name} PASÓ: {key} == {expected!r}")
    else:
        print(f"   ❌  {test_name} FALLÓ: esperado {expected!r}, obtenido {actual!r}")


# ─────────────────────────────────────────────────────────────────────────────
# MODO INTERACTIVO: enviar frames desde una imagen local
# ─────────────────────────────────────────────────────────────────────────────

def run_from_image(image_path: str, mode: str, server_ip: str):
    """Carga una imagen del disco y la envía al servidor en bucle."""
    frame = cv2.imread(image_path)
    if frame is None:
        print(f"[MOCK] ERROR: no se pudo cargar '{image_path}'")
        return

    frame = cv2.resize(frame, (FRAME_W, FRAME_H))
    client = MockClient(server_ip)
    client.connect()

    print(f"[MOCK] Enviando '{image_path}' en modo {mode} — Ctrl+C para detener.")
    try:
        count = 0
        while True:
            result = client.send(mode, frame)
            count += 1
            if result:
                print(f"  [{count}] {result}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[MOCK] Detenido.")
    finally:
        client.close()


# ─────────────────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Uso: python mock_client.py [IP] [--image ruta_imagen modo]
    args = sys.argv[1:]

    server_ip = DEFAULT_SERVER_IP
    if args and not args[0].startswith("--"):
        server_ip = args[0]
        args = args[1:]

    if "--image" in args:
        idx  = args.index("--image")
        path = args[idx + 1] if idx + 1 < len(args) else None
        mode = args[idx + 2] if idx + 2 < len(args) else "HAND"
        if path:
            run_from_image(path, mode.upper(), server_ip)
        else:
            print("Uso: python mock_client.py [IP] --image ruta_imagen [HAND|QR|BOTH]")
    else:
        run_test(server_ip)
