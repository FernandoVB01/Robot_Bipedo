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

import os
import json
from pathlib import Path

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
# Cargar config.json para elegir cámara/red sin tocar código
_CFG_PATH = Path(__file__).parent.parent / "config.json"
try:
    _CFG = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
except Exception:
    _CFG = {}
_V = _CFG.get("vision", {})

# El transporte RTSP se fija ANTES de importar cv2: OpenCV lee esta variable de
# entorno cuando inicializa FFMPEG, no en cada VideoCapture. Sobre el WiFi de un
# hotspot, RTSP por UDP pierde paquetes y la imagen llega rota en bloques.
if _V.get("camara_rtsp_tcp", True):
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS",
                          "rtsp_transport;tcp|stimeout;5000000")

import cv2
import zmq
import time
import threading
import urllib.request
import numpy as np
from queue import Queue, Empty

# La IP de la PC cambia cada vez que se reinicia el hotspot del celular. Para no
# tener que editar config.json en cada sesión, ROBOT_PC_IP la pisa:
#     ROBOT_PC_IP=192.168.43.57 python3 pi/pi_gui_gpio.py
DEFAULT_PC_IP      = os.environ.get("ROBOT_PC_IP") \
                     or _CFG.get("red", {}).get("pc_ip", "192.168.43.50")
DEFAULT_PORT       = _CFG.get("red", {}).get("zmq_puerto", 5555)
JPEG_QUALITY       = _V.get("jpeg_calidad", 60)   # Compresión para bajo ancho de banda
FRAME_WIDTH        = _V.get("camara_ancho", 320)  # Resolución reducida para menor latencia
FRAME_HEIGHT       = _V.get("camara_alto", 240)
CAMERA_FPS         = _V.get("camara_fps", 15)
CAMERA_INDEX       = _V.get("camara_index", 0)    # /dev/video0 (webcam local)
CAMERA_URL         = _V.get("camara_url", "")     # Si está definido, usa una cámara IP (Foscam / celular)
CAMERA_MODE        = _V.get("camara_modo", "stream").lower()   # "stream" | "snapshot"
SNAPSHOT_URL       = _V.get("camara_snapshot_url", "")
CAMERA_RETRY_S     = _V.get("camara_reintento_s", 3.0)
USE_SNAPSHOT       = bool(CAMERA_URL) and CAMERA_MODE == "snapshot" and bool(SNAPSHOT_URL)
RECONNECT_DELAY_S  = 3.0              # Segundos entre reintentos de conexión ZeroMQ


def _url_sin_clave(url: str) -> str:
    """Oculta usuario:clave de una URL RTSP para no imprimirla en los logs."""
    if "@" in url and "//" in url:
        esquema, resto = url.split("//", 1)
        return f"{esquema}//***:***@{resto.split('@', 1)[1]}"
    return url


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
        self._grab_thread = None
        self._last_frame  = None
        self._frame_lock  = threading.Lock()

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
        if mode not in ("HAND", "QR", "BOTH", "TODO", "PERSONA"):
            raise ValueError(f"Modo inválido: {mode}. Usar HAND, QR, BOTH, TODO o PERSONA.")
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
        """
        Abre la cámara. Dos modos según config.json:
          - camara_url vacío  → webcam/cámara local (/dev/video<index>) vía V4L2.
          - camara_url con URL → cámara IP (Foscam por RTSP, o celular por HTTP).
        """
        if USE_SNAPSHOT:
            # Modo foto suelta: no hay stream que abrir, solo se comprueba que la
            # cámara responda antes de arrancar el hilo que pide imágenes.
            print(f"[PI-CLIENT] Cámara en modo snapshot: {_url_sin_clave(SNAPSHOT_URL)}")
            if self._pedir_snapshot() is None:
                print("[PI-CLIENT] ERROR: la cámara no respondió al snapshot. "
                      "Comprobá IP, puerto, usuario y clave abriendo esa misma URL "
                      "en un navegador.")
                return False
            self._start_grabber()
            print("[PI-CLIENT] Cámara IP lista (snapshot).")
            return True

        if CAMERA_URL:
            # Cámara IP (Foscam/celular). Backend FFMPEG explícito para streams de red.
            print(f"[PI-CLIENT] Abriendo cámara IP: {_url_sin_clave(CAMERA_URL)}")
            self._cap = cv2.VideoCapture(CAMERA_URL, cv2.CAP_FFMPEG)
            if not self._cap.isOpened():
                print(f"[PI-CLIENT] ERROR: No se pudo abrir la cámara IP. "
                      f"Comprobá: cable/WiFi de la cámara, IP correcta, usuario y "
                      f"clave, y que el RTSP esté habilitado en la cámara.")
                return False
            # Buffer de 1 frame: sin esto el stream RTSP se acumula en la cola de
            # FFMPEG y el retardo crece hasta varios segundos (la mano ya no está
            # cuando el frame llega a MediaPipe).
            try:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            self._start_grabber()
            print("[PI-CLIENT] Cámara IP lista.")
            return True

        # Webcam / cámara local
        self._cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            print("[PI-CLIENT] ERROR: No se pudo abrir la cámara.")
            return False
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        self._cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
        print(f"[PI-CLIENT] Cámara lista: {FRAME_WIDTH}×{FRAME_HEIGHT}")
        return True

    # ── Lectura de cámara IP ────────────────────────────────────────────────
    #
    # Una cámara IP empuja frames a su propio ritmo (25 fps en la Foscam), pero
    # el ciclo capturar→JPEG→ZeroMQ→MediaPipe va mucho más lento. Si se llamara
    # read() dentro de ese ciclo, cada lectura devolvería el frame más viejo de
    # la cola y el retardo crecería sin parar. Este hilo consume el stream a
    # velocidad completa y guarda solo el último frame; el ciclo principal
    # siempre trabaja con imagen fresca.

    def _start_grabber(self):
        self._grab_thread = threading.Thread(target=self._grabber_loop, daemon=True,
                                             name="CameraGrabberThread")
        self._grab_thread.start()

    def _pedir_snapshot(self):
        """Descarga un JPEG de snapshot.cgi y lo decodifica. None si falla."""
        try:
            with urllib.request.urlopen(SNAPSHOT_URL, timeout=3) as resp:
                datos = resp.read()
            if not datos:
                return None
            frame = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8),
                                 cv2.IMREAD_COLOR)
            return frame
        except Exception:
            return None

    def _grabber_loop(self):
        if USE_SNAPSHOT:
            self._grabber_loop_snapshot()
            return
        self._grabber_loop_stream()

    def _grabber_loop_snapshot(self):
        """Pide fotos sueltas a la cámara al ritmo de camara_fps."""
        periodo = 1.0 / max(CAMERA_FPS, 1)
        fallos  = 0
        while self._running:
            t0 = time.time()
            frame = self._pedir_snapshot()
            if frame is None:
                fallos += 1
                if fallos % 10 == 1:
                    print("[PI-CLIENT] WARN: la cámara no responde al snapshot…")
                time.sleep(CAMERA_RETRY_S)
                continue
            fallos = 0
            with self._frame_lock:
                self._last_frame = frame
            # Ritmo constante: no saturar a la FI8918W con peticiones seguidas.
            time.sleep(max(0.0, periodo - (time.time() - t0)))

    def _grabber_loop_stream(self):
        fallos = 0
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                time.sleep(CAMERA_RETRY_S)
                self._reabrir_camara()
                continue
            ok, frame = self._cap.read()
            if not ok:
                fallos += 1
                # Un par de frames perdidos es normal en WiFi; una racha larga
                # significa que el stream se cortó y hay que reabrirlo.
                if fallos >= 30:
                    print("[PI-CLIENT] Stream caído — reabriendo cámara IP…")
                    self._reabrir_camara()
                    fallos = 0
                time.sleep(0.05)
                continue
            fallos = 0
            with self._frame_lock:
                self._last_frame = frame

    def _reabrir_camara(self):
        try:
            if self._cap:
                self._cap.release()
        except Exception:
            pass
        time.sleep(CAMERA_RETRY_S)
        self._cap = cv2.VideoCapture(CAMERA_URL, cv2.CAP_FFMPEG)
        if self._cap.isOpened():
            try:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            print("[PI-CLIENT] Cámara IP reconectada.")

    def _read_frame(self):
        """Devuelve (ok, frame). Con cámara IP toma el último frame del grabber."""
        if CAMERA_URL:
            with self._frame_lock:
                frame = self._last_frame
                self._last_frame = None      # No reenviar dos veces el mismo frame
            return (frame is not None), frame
        return self._cap.read()

    def _compress_frame(self, frame: np.ndarray) -> bytes:
        """
        Reescala a la resolución de trabajo y convierte a JPEG en memoria.

        El reescalado importa sobre todo con la Foscam: entrega 1280×720 (o más)
        y mandar eso entero multiplicaría por ~10 el tamaño del JPEG y el tiempo
        de MediaPipe, sin mejorar la detección de mano ni de QR a esta distancia.
        """
        h, w = frame.shape[:2]
        if (w, h) != (FRAME_WIDTH, FRAME_HEIGHT):
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT),
                               interpolation=cv2.INTER_AREA)
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
            ret, frame = self._read_frame()
            if not ret:
                # Con cámara IP esto solo significa "todavía no llegó un frame
                # nuevo del grabber": es normal y no hay que loguearlo.
                time.sleep(0.02)
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
