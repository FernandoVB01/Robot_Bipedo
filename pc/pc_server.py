"""
=============================================================================
ROBOT BÍPEDO — SERVIDOR PC  (ZeroMQ + MediaPipe + QR + Base de Datos)
=============================================================================
Rol       : Cerebro pesado. Recibe frames comprimidos de la Raspberry Pi,
            ejecuta visión por computadora y devuelve banderas de control.
            Registra cada interacción exitosa en la base de datos SQLite.

Protocolo ZeroMQ REQ/REP:
  Pi  → PC  : multipart [modo_bytes, frame_jpeg_bytes]
               modo: b"HAND" | b"QR" | b"BOTH"
  PC  → Pi  : JSON  {"hand_detected": bool, "qr_data": str|null, "error": str|null}

Registros en BD (solo cuando mode=="QR" y se decodifica el QR):
  - Crea/actualiza cliente por cédula
  - Registra transacción con precio, descuento y resultado

Dependencias (PC):
    pip install pyzmq opencv-python mediapipe numpy sqlalchemy

Ejecución:
    python pc_server.py
    # Ctrl+C para detener ordenadamente
=============================================================================
"""

import json
import time
import threading
import urllib.request
import cv2
import zmq
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision
from pathlib import Path

# ──────────────────────────────────────────────
# CARGA DE CONFIGURACIÓN
# ──────────────────────────────────────────────
_CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[PC-SERVER] WARN: {_CONFIG_PATH} no encontrado — usando defaults.")
        return {}

CFG = _load_config()

# Parámetros de red
_ZMQ_PORT   = CFG.get("red", {}).get("zmq_puerto", 5555)
BIND_ADDR   = f"tcp://*:{_ZMQ_PORT}"

# Parámetros de visión
_V = CFG.get("vision", {})
JPEG_QUALITY             = _V.get("jpeg_calidad", 60)
PALM_MIN_FINGERS         = _V.get("dedos_minimos_palma_abierta", 4)
MP_DETECTION_CONFIDENCE  = _V.get("mediapipe_confianza_deteccion", 0.70)
MP_TRACKING_CONFIDENCE   = _V.get("mediapipe_confianza_seguimiento", 0.50)
QR_SECOND_PASS_OTSU      = _V.get("qr_segunda_pasada_otsu", True)

# ──────────────────────────────────────────────
# BASE DE DATOS (importación diferida para
# evitar error si SQLAlchemy no está instalado)
# ──────────────────────────────────────────────
try:
    from database import db as _db
    DB_AVAILABLE = True
    _db.seed_from_config()
except ImportError:
    DB_AVAILABLE = False
    _db = None
    print("[PC-SERVER] WARN: database.py no encontrado — sin registro en BD.")

# ──────────────────────────────────────────────
# FIREBASE (espejo en la nube, opcional)
# Importación resiliente: si falta la librería, las credenciales o internet,
# el robot sigue funcionando con SQLite y las facturas quedan pendientes de
# subir (se reintentan con sync_firebase.py).
# ──────────────────────────────────────────────
try:
    from firebase_client import fb as _fb
    FIREBASE_AVAILABLE = True
except Exception as _exc:   # noqa: BLE001 — nunca debe tumbar el servidor
    FIREBASE_AVAILABLE = False
    _fb = None
    print(f"[PC-SERVER] WARN: firebase_client no disponible — {_exc}")

# ──────────────────────────────────────────────
# MEDIAPIPE — Detección de manos (Tasks API, mediapipe >= 0.10.30)
# ──────────────────────────────────────────────
_MODEL_PATH = Path(__file__).parent / "hand_landmarker.task"
_MODEL_URL  = ("https://storage.googleapis.com/mediapipe-models/"
               "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")

def _ensure_model():
    """Descarga el modelo si no existe localmente."""
    if not _MODEL_PATH.exists():
        print(f"[PC-SERVER] Descargando modelo MediaPipe (~25 MB)...")
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
        print(f"[PC-SERVER] Modelo guardado en {_MODEL_PATH}")

_ensure_model()

_base_opts   = mp_tasks.BaseOptions(model_asset_path=str(_MODEL_PATH))
_hand_opts   = mp_vision.HandLandmarkerOptions(
    base_options                  = _base_opts,
    num_hands                     = 1,
    min_hand_detection_confidence = MP_DETECTION_CONFIDENCE,
    min_hand_presence_confidence  = MP_TRACKING_CONFIDENCE,
    min_tracking_confidence       = MP_TRACKING_CONFIDENCE,
)
_hands_detector = mp_vision.HandLandmarker.create_from_options(_hand_opts)

_FINGER_TIP = [8,  12, 16, 20]
_FINGER_PIP = [6,  10, 14, 18]

# Landmarks del pulgar
_THUMB_TIP = 4   # Punta del pulgar
_THUMB_IP  = 3   # Articulación IP
_THUMB_MCP = 2   # Articulación MCP (base del pulgar)

# ──────────────────────────────────────────────
# OPENCV — Detector QR
# ──────────────────────────────────────────────
_qr_detector = cv2.QRCodeDetector()

# ──────────────────────────────────────────────
# ESTADÍSTICAS EN VIVO (accesibles desde la API)
# ──────────────────────────────────────────────
stats_lock = threading.Lock()
live_stats = {
    "frames_procesados": 0,
    "detecciones_mano":  0,
    "pulgares_arriba":   0,
    "qr_decodificados":  0,
    "errores":           0,
    "inicio":            time.time(),
    "ultimo_frame_ts":   None,
}


def _inc(key: str, n: int = 1):
    with stats_lock:
        live_stats[key] = live_stats.get(key, 0) + n
        live_stats["ultimo_frame_ts"] = time.time()


def get_live_stats() -> dict:
    with stats_lock:
        s = dict(live_stats)
    uptime = time.time() - s["inicio"]
    s["uptime_segundos"] = round(uptime, 1)
    s["fps_promedio"]    = round(s["frames_procesados"] / max(uptime, 1), 2)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONES DE VISIÓN
# ─────────────────────────────────────────────────────────────────────────────

def decode_frame(frame_bytes: bytes) -> np.ndarray | None:
    """Descomprime JPEG recibido → array NumPy BGR."""
    arr = np.frombuffer(frame_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return frame


def detect_open_palm(frame: np.ndarray) -> bool:
    """
    True si MediaPipe detecta una palma abierta (≥ PALM_MIN_FINGERS dedos extendidos).
    Criterio: tip.y < pip.y en coordenadas de imagen normalizadas.
    Usa la nueva Tasks API (mediapipe >= 0.10.30).
    """
    rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result   = _hands_detector.detect(mp_image)

    if not result.hand_landmarks:
        return False

    for hand_landmarks in result.hand_landmarks:
        # hand_landmarks es una lista de objetos con atributos .x .y .z
        extended = sum(
            1 for tip_i, pip_i in zip(_FINGER_TIP, _FINGER_PIP)
            if hand_landmarks[tip_i].y < hand_landmarks[pip_i].y
        )
        if extended >= PALM_MIN_FINGERS:
            return True

    return False


def detect_thumbs_up(frame: np.ndarray) -> bool:
    """
    True si MediaPipe detecta un pulgar arriba (👍).
    Criterio:
      - Pulgar extendido: tip(4).y < IP(3).y < MCP(2).y  (apunta hacia arriba)
      - Al menos 3 de los otros 4 dedos están cerrados: tip.y > pip.y
    """
    rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result   = _hands_detector.detect(mp_image)

    if not result.hand_landmarks:
        return False

    for lm in result.hand_landmarks:
        thumb_up = (lm[_THUMB_TIP].y < lm[_THUMB_IP].y < lm[_THUMB_MCP].y)
        fingers_curled = sum(
            1 for tip_i, pip_i in zip(_FINGER_TIP, _FINGER_PIP)
            if lm[tip_i].y > lm[pip_i].y
        )
        if thumb_up and fingers_curled >= 3:
            return True

    return False


def decode_qr(frame: np.ndarray) -> str | None:
    """
    Intenta decodificar un QR en el frame.
    Primera pasada: imagen en color.
    Segunda pasada: escala de grises + umbral Otsu (mejora QRs con bajo contraste).
    """
    data, _, _ = _qr_detector.detectAndDecode(frame)
    if data:
        return data

    if QR_SECOND_PASS_OTSU:
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        data, _, _ = _qr_detector.detectAndDecode(th)
        if data:
            return data

    return None


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIÓN DE REGISTRO EN BD
# ─────────────────────────────────────────────────────────────────────────────

def _registrar_en_bd(cedula: str | None, qr_data: str | None):
    """
    Registra la interacción en la base de datos si los datos son válidos.
    Se ejecuta en un hilo separado para no bloquear el bucle ZeroMQ.
    """
    if not DB_AVAILABLE or not cedula or not qr_data:
        return

    def _worker():
        try:
            # Validar el QR en la BD y obtener producto
            qr_info = _db.validar_y_usar_qr(qr_data)

            if qr_info:
                prod = _db.get_producto_by_id(qr_info["producto_id"])
                trans = _db.registrar_transaccion(
                    cedula      = cedula,
                    qr_codigo   = qr_data,
                    producto_id = qr_info["producto_id"],
                    precio_base = prod["precio_base"] if prod else None,
                    descuento   = prod["descuento"]   if prod else None,
                    exito       = True,
                )
                print(f"[DB] Transacción registrada: cédula={cedula} "
                      f"producto={prod['nombre'] if prod else 'N/A'}")
            else:
                # QR no encontrado o ya usado: registrar como fallido
                trans = _db.registrar_transaccion(
                    cedula      = cedula,
                    qr_codigo   = qr_data,
                    producto_id = None,
                    precio_base = None,
                    descuento   = None,
                    exito       = False,
                )
                print(f"[DB] QR inválido registrado: cédula={cedula}")

            # ── Escritura doble: espejo en la nube (Firebase) ──
            # SQLite es la fuente de verdad; Firebase es la copia consultable.
            # Si falla (sin internet, sin credenciales), la transacción queda
            # con sincronizado_firebase=False y sync_firebase.py la reintenta.
            if FIREBASE_AVAILABLE and _fb is not None and _fb.esta_disponible():
                if _fb.guardar_transaccion(trans):
                    _db.marcar_sincronizado(trans["id"])
                    print(f"[FIREBASE] Transacción {trans['id']} subida a la nube.")
                else:
                    print(f"[FIREBASE] Transacción {trans['id']} quedó pendiente "
                          f"de subir (se reintentará luego).")

        except Exception as exc:
            print(f"[DB] ERROR al registrar transacción: {exc}")

    threading.Thread(target=_worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# ESTADO DE SESIÓN ACTIVA
# Estado del robot que el servidor trackea para asociar cédula + QR
# ─────────────────────────────────────────────────────────────────────────────
_session_lock    = threading.Lock()
_active_cedula: str | None = None   # Cédula en curso (guardada cuando la Pi la envía)


def set_active_cedula(cedula: str):
    global _active_cedula
    with _session_lock:
        _active_cedula = cedula


def get_active_cedula() -> str | None:
    with _session_lock:
        return _active_cedula


# ─────────────────────────────────────────────────────────────────────────────
# BUCLE PRINCIPAL DEL SERVIDOR
# ─────────────────────────────────────────────────────────────────────────────

def run_server():
    context = zmq.Context()
    socket  = context.socket(zmq.REP)
    socket.bind(BIND_ADDR)
    print(f"[PC-SERVER] Escuchando en {BIND_ADDR}")
    print(f"[PC-SERVER] BD disponible: {DB_AVAILABLE}")
    print(f"[PC-SERVER] Parámetros visión: "
          f"palm_min_fingers={PALM_MIN_FINGERS} "
          f"mp_conf={MP_DETECTION_CONFIDENCE}")

    try:
        while True:
            # ── 1. Recibir ────────────────────────────────────────────────
            parts = socket.recv_multipart()

            if len(parts) == 3:
                # Modo extendido: [modo, frame, cedula_bytes]
                mode_bytes, frame_bytes, cedula_bytes = parts
                cedula_recibida = cedula_bytes.decode("utf-8").strip()
                if cedula_recibida:
                    set_active_cedula(cedula_recibida)
            elif len(parts) == 2:
                mode_bytes, frame_bytes = parts
            else:
                socket.send_json({"error": "formato_invalido",
                                  "hand_detected": False, "qr_data": None})
                continue

            mode = mode_bytes.decode("utf-8").strip().upper()

            # ── 2. Descomprimir frame ─────────────────────────────────────
            frame = decode_frame(frame_bytes)
            if frame is None:
                socket.send_json({"error": "frame_corrupto",
                                  "hand_detected": False, "qr_data": None})
                _inc("errores")
                continue

            _inc("frames_procesados")
            response: dict = {"hand_detected": False, "thumbs_up": False,
                               "qr_data": None, "error": None}

            # ── 3. Procesar según modo ────────────────────────────────────
            if mode == "HAND":
                # Detectar palma abierta (STOP) y pulgar arriba (INTERACCIÓN)
                detected  = detect_open_palm(frame)
                thumb_up  = detect_thumbs_up(frame) if not detected else False
                response["hand_detected"] = detected
                response["thumbs_up"]     = thumb_up
                if detected:
                    _inc("detecciones_mano")
                if thumb_up:
                    _inc("pulgares_arriba")

            elif mode == "QR":
                qr = decode_qr(frame)  # noqa
                response["qr_data"] = qr
                if qr:
                    _inc("qr_decodificados")
                    cedula = get_active_cedula()
                    _registrar_en_bd(cedula, qr)

            elif mode == "BOTH":
                detected = detect_open_palm(frame)
                thumb_up = detect_thumbs_up(frame) if not detected else False
                qr       = decode_qr(frame)
                response["hand_detected"] = detected
                response["thumbs_up"]     = thumb_up
                response["qr_data"]       = qr
                if detected:  _inc("detecciones_mano")
                if thumb_up:  _inc("pulgares_arriba")
                if qr:
                    _inc("qr_decodificados")
                    _registrar_en_bd(get_active_cedula(), qr)

            # ── 4. Responder ──────────────────────────────────────────────
            socket.send_json(response)

            # Log cada 60 frames
            if live_stats["frames_procesados"] % 60 == 0:
                s = get_live_stats()
                print(f"[PC-SERVER] frames={s['frames_procesados']} "
                      f"fps={s['fps_promedio']} "
                      f"manos={s['detecciones_mano']} "
                      f"qrs={s['qr_decodificados']}")

    except KeyboardInterrupt:
        print("\n[PC-SERVER] Detenido.")
    finally:
        socket.close()
        context.term()


if __name__ == "__main__":
    run_server()
