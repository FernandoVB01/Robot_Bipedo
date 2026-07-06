#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT BÍPEDO — CLIENTE ZeroMQ (Raspberry Pi)
=============================================================================
Entorno virtual : /home/pi/mi_proyecto_env
Activar antes de ejecutar:
    source ~/mi_proyecto_env/bin/activate

Rol       : Captura frames de la cámara, los comprime y los manda al servidor
            PC para su análisis. Devuelve las banderas de control al resto
            del sistema mediante una cola thread-safe.
Protocolo : ZeroMQ REQ  (espeja el REP del pc_server.py)
  - Envía  : multipart [modo_bytes, frame_jpeg_bytes]
  - Recibe : JSON {"hand_detected": bool, "qr_data": str|null}

Uso típico (desde pi_gui_gpio.py o pi_main.py):
    from pi_zmq_client import VisionClient
    client = VisionClient("192.168.1.100")   # IP de tu PC
    client.start()
    result = client.get_last_result()        # {"hand_detected":…, "qr_data":…}
    client.set_mode("QR")                    # Cambia el modo en caliente
    client.stop()

Dependencias:
    ✅ Ya instaladas en el entorno:
        pyzmq

    ⚠️  Pendientes de instalar (ejecutar con el entorno activo):
        pip install opencv-python numpy
=============================================================================
"""

import cv2
import zmq
import json
import time
import threading
import numpy as np
from queue import Queue, Empty

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
DEFAULT_PC_IP      = "192.168.1.100"   # ← Cambia a la IP real de tu PC
DEFAULT_PORT       = 5555
JPEG_QUALITY       = 60               # Compresión agresiva para bajo ancho de banda
FRAME_WIDTH        = 320              # Resolución reducida para menor latencia
FRAME_HEIGHT       = 240
CAMERA_INDEX       = 0                # /dev/video0
RECONNECT_DELAY_S  = 3.0              # Segundos entre reintentos de conexión


class VisionClient:
    """
    Hilo productor: captura → comprime → envía → recibe → almacena resultado.
    Thread-safe mediante un Queue de resultados y una variable de modo.
    """

    def __init__(self, pc_ip: str = DEFAULT_PC_IP, port: int = DEFAULT_PORT):
        self._pc_address = f"tcp://{pc_ip}:{port}"
        self._mode       = "HAND"           # "HAND" | "QR" | "BOTH"
        self._mode_lock  = threading.Lock()
        self._result_q   = Queue(maxsize=5)  # Guarda los últimos resultados
        self._running    = False
        self._thread     = None
        self._cap        = None

    # ── API PÚBLICA ──────────────────────────────────────────────────────────

    def start(self):
        """Inicia el hilo de captura y comunicación."""
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True,
                                         name="VisionClientThread")
        self._thread.start()
        print(f"[PI-CLIENT] VisionClient iniciado → {self._pc_address}")

    def stop(self):
        """Detiene el hilo ordenadamente."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._cap and self._cap.isOpened():
            self._cap.release()
        print("[PI-CLIENT] VisionClient detenido.")

    def set_mode(self, mode: str):
        """Cambia el modo de análisis en caliente: 'HAND', 'QR' o 'BOTH'."""
        mode = mode.upper()
        if mode not in ("HAND", "QR", "BOTH"):
            raise ValueError(f"Modo inválido: {mode}. Usar HAND, QR o BOTH.")
        with self._mode_lock:
            self._mode = mode

    def get_last_result(self, timeout: float = 0.1) -> dict | None:
        """
        Devuelve el resultado más reciente o None si no hay datos.
        Vacía la cola para quedarse solo con el último resultado fresco.
        """
        result = None
        try:
            while True:                        # Vaciar cola, quedarse con el último
                result = self._result_q.get_nowait()
        except Empty:
            pass
        return result

    # ── HILO INTERNO ─────────────────────────────────────────────────────────

    def _init_camera(self) -> bool:
        """Abre la cámara y configura la resolución."""
        self._cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            print("[PI-CLIENT] ERROR: No se pudo abrir la cámara.")
            return False
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        self._cap.set(cv2.CAP_PROP_FPS, 15)
        print(f"[PI-CLIENT] Cámara lista: {FRAME_WIDTH}×{FRAME_HEIGHT}")
        return True

    def _compress_frame(self, frame: np.ndarray) -> bytes:
        """Convierte el frame BGR a JPEG comprimido en memoria."""
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        _, buffer = cv2.imencode(".jpg", frame, encode_param)
        return buffer.tobytes()

    def _loop(self):
        """Bucle principal del hilo: conecta ZMQ, captura y procesa frames."""
        if not self._init_camera():
            return

        context = zmq.Context()
        socket  = context.socket(zmq.REQ)
        socket.setsockopt(zmq.RCVTIMEO, 3000)   # Timeout recepción: 3 s
        socket.setsockopt(zmq.SNDTIMEO, 3000)   # Timeout envío:     3 s
        socket.setsockopt(zmq.LINGER,   0)       # No bloquear al cerrar
        socket.connect(self._pc_address)
        print(f"[PI-CLIENT] Conectado a {self._pc_address}")

        while self._running:
            # ── 1. Capturar frame ─────────────────────────────────────────
            ret, frame = self._cap.read()
            if not ret:
                print("[PI-CLIENT] WARN: frame vacío, reintentando…")
                time.sleep(0.05)
                continue

            # ── 2. Comprimir ──────────────────────────────────────────────
            frame_bytes = self._compress_frame(frame)

            # ── 3. Obtener modo actual de forma thread-safe ───────────────
            with self._mode_lock:
                mode = self._mode

            # ── 4. Enviar al servidor ─────────────────────────────────────
            try:
                socket.send_multipart([mode.encode("utf-8"), frame_bytes])
                raw   = socket.recv()
                result = json.loads(raw.decode("utf-8"))

                # ── 5. Depositar resultado en cola ────────────────────────
                if self._result_q.full():
                    try:
                        self._result_q.get_nowait()   # Descartar resultado viejo
                    except Empty:
                        pass
                self._result_q.put_nowait(result)

            except zmq.Again:
                print("[PI-CLIENT] WARN: timeout de red, reconectando…")
                # Reconectar socket para evitar estado ZMQ bloqueado
                socket.close()
                time.sleep(RECONNECT_DELAY_S)
                socket = context.socket(zmq.REQ)
                socket.setsockopt(zmq.RCVTIMEO, 3000)
                socket.setsockopt(zmq.SNDTIMEO, 3000)
                socket.setsockopt(zmq.LINGER,   0)
                socket.connect(self._pc_address)

            except Exception as exc:
                print(f"[PI-CLIENT] ERROR inesperado: {exc}")
                time.sleep(0.5)

        # ── Limpieza ──────────────────────────────────────────────────────
        socket.close()
        context.term()
        if self._cap:
            self._cap.release()


# ─────────────────────────────────────────────────────────────────────────────
# Prueba rápida (ejecutar directamente en la Pi para verificar la conexión)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    pc_ip = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PC_IP
    client = VisionClient(pc_ip=pc_ip)
    client.start()

    print("[TEST] Enviando frames en modo HAND por 10 segundos…")
    try:
        for _ in range(100):
            time.sleep(0.1)
            result = client.get_last_result()
            if result:
                print(f"  → hand_detected={result.get('hand_detected')}  "
                      f"qr_data={result.get('qr_data')}")
    finally:
        client.stop()
