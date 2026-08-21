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
PERSONA_ACTIVA           = _V.get("persona_activa", True)
PERSONA_CONFIANZA        = _V.get("persona_confianza", 0.45)

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

# ¿Registrar la venta aquí, al decodificar el QR?
# En false (por defecto) la registra solo la API cuando la Pi hace POST
# /api/transacciones al terminar el flujo, que es el único momento en que se
# conoce la cédula real del cliente. Registrar en los dos sitios duplicaba cada
# venta, y la segunda entraba como fallida porque el QR de uso único ya estaba
# consumido por la primera.
REGISTRAR_DESDE_ZMQ = CFG.get("base_de_datos", {}).get("registrar_desde_zmq", False)

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

# ──────────────────────────────────────────────
# MEDIAPIPE — Detección de PERSONAS (silueta completa)
#
# Antes el robot solo sabía reconocer una MANO: el cliente tenía que acercarse y
# saludar. Para que el robot vaya él hacia la persona hace falta detectar el
# cuerpo entero y, sobre todo, saber DÓNDE está y CUÁN LEJOS.
#
# Se usa EfficientDet-Lite0 (detector de objetos) filtrando la clase "person",
# en vez de Pose Landmarker, porque lo que necesita el lazo de acercamiento es
# justamente un RECUADRO:
#   · el centro horizontal del recuadro → hacia dónde girar
#   · la altura del recuadro            → a qué distancia está
# Los 33 puntos del esqueleto que daría Pose no aportan nada a eso y cuestan más.
# ──────────────────────────────────────────────
_MODELO_PERSONA      = Path(__file__).parent / "efficientdet_lite0.tflite"
_MODELO_PERSONA_URL  = ("https://storage.googleapis.com/mediapipe-models/"
                        "object_detector/efficientdet_lite0/float32/1/"
                        "efficientdet_lite0.tflite")

_detector_persona = None

def _init_detector_persona():
    """
    Prepara el detector de personas. Si algo falla (sin internet la primera vez,
    modelo corrupto), NO tumba el servidor: el robot sigue funcionando con el
    disparo por gesto de mano de siempre.
    """
    global _detector_persona
    if not PERSONA_ACTIVA:
        print("[PC-SERVER] Detección de personas desactivada por configuración.")
        return
    try:
        if not _MODELO_PERSONA.exists():
            print("[PC-SERVER] Descargando modelo de personas (~14 MB)…")
            urllib.request.urlretrieve(_MODELO_PERSONA_URL, _MODELO_PERSONA)
            print(f"[PC-SERVER] Modelo guardado en {_MODELO_PERSONA}")

        _detector_persona = mp_vision.ObjectDetector.create_from_options(
            mp_vision.ObjectDetectorOptions(
                base_options       = mp_tasks.BaseOptions(
                                        model_asset_path=str(_MODELO_PERSONA)),
                max_results        = 5,
                score_threshold    = PERSONA_CONFIANZA,
                category_allowlist = ["person"],
            ))
        print("[PC-SERVER] Detector de personas listo.")
    except Exception as exc:   # noqa: BLE001 — nunca debe tumbar el servidor
        _detector_persona = None
        print(f"[PC-SERVER] WARN: sin detección de personas — {exc}")
        print("[PC-SERVER]       El robot seguirá disparando con el gesto de mano.")

_init_detector_persona()

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
    "personas_detectadas": 0,
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


_HAND_CENTER = 9    # MCP del dedo medio: el punto más estable como "centro" de la mano


def analizar_mano(frame: np.ndarray) -> dict:
    """
    Corre MediaPipe UNA sola vez y saca de ahí todo lo que necesita el robot:
    palma abierta, pulgar arriba y posición de la mano en la imagen.

    Antes había dos funciones que llamaban cada una a _hands_detector.detect():
    en una PC daba igual, pero corriendo en la Raspberry eso era ejecutar la red
    neuronal dos veces por frame y partía los fps por la mitad.

    Devuelve:
      palma      — bool, ≥ PALM_MIN_FINGERS dedos extendidos
      pulgar     — bool, 👍
      x, y       — centro de la mano en coordenadas normalizadas 0.0-1.0
                   (None si no se detectó ninguna mano). Los usa la Pi para
                   apuntar la cámara motorizada hacia el cliente.
    """
    rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result   = _hands_detector.detect(mp_image)

    salida = {"palma": False, "pulgar": False, "x": None, "y": None}
    if not result.hand_landmarks:
        return salida

    for lm in result.hand_landmarks:
        extended = sum(
            1 for tip_i, pip_i in zip(_FINGER_TIP, _FINGER_PIP)
            if lm[tip_i].y < lm[pip_i].y
        )
        palma = extended >= PALM_MIN_FINGERS

        thumb_up = (lm[_THUMB_TIP].y < lm[_THUMB_IP].y < lm[_THUMB_MCP].y)
        fingers_curled = sum(
            1 for tip_i, pip_i in zip(_FINGER_TIP, _FINGER_PIP)
            if lm[tip_i].y > lm[pip_i].y
        )
        pulgar = thumb_up and fingers_curled >= 3

        if palma or pulgar:
            salida["palma"]  = palma
            # Palma abierta manda: si es palma, no se reporta pulgar (igual que antes)
            salida["pulgar"] = pulgar and not palma
            salida["x"] = float(lm[_HAND_CENTER].x)
            salida["y"] = float(lm[_HAND_CENTER].y)
            return salida

    # Hay mano pero sin gesto reconocido: igual sirve para seguirla con la cámara
    lm = result.hand_landmarks[0]
    salida["x"] = float(lm[_HAND_CENTER].x)
    salida["y"] = float(lm[_HAND_CENTER].y)
    return salida


def analizar_persona(frame: np.ndarray) -> dict:
    """
    Busca personas en el frame y devuelve la MÁS GRANDE (la más cercana).

    Devuelve:
      detectada — bool
      x         — centro horizontal del recuadro, normalizado 0.0-1.0.
                  0.5 = justo enfrente. La Pi lo usa para saber hacia qué lado
                  girar mientras se acerca.
      alto      — altura del recuadro como fracción del encuadre (0.0-1.0).
                  Es el indicador de DISTANCIA: cuanto más cerca está la
                  persona, más alto se ve. No son metros, y no hace falta que lo
                  sean — se calibra una vez parándose a la distancia deseada y
                  anotando el valor (la Pi lo va imprimiendo).
      score     — confianza del detector

    Si hay varias personas se elige la de mayor área: es la que está más cerca,
    o sea a la que el robot le tiene que hablar.
    """
    salida = {"detectada": False, "x": None, "y": None,
              "alto": None, "score": None}
    if _detector_persona is None:
        return salida

    alto_img, ancho_img = frame.shape[:2]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    resultado = _detector_persona.detect(mp_image)

    if not resultado.detections:
        return salida

    mejor = max(resultado.detections,
                key=lambda d: d.bounding_box.width * d.bounding_box.height)
    bb = mejor.bounding_box

    salida["detectada"] = True
    salida["x"]     = float((bb.origin_x + bb.width / 2.0) / ancho_img)
    # Para apuntar la cámara motorizada interesa la CARA, no el centro del
    # cuerpo: se toma el tercio superior del recuadro. Si se usara el centro,
    # la Foscam terminaría enfocando el ombligo del cliente.
    salida["y"]     = float((bb.origin_y + bb.height * 0.28) / alto_img)
    salida["alto"]  = float(bb.height / alto_img)
    salida["score"] = float(mejor.categories[0].score) if mejor.categories else None
    return salida


def decode_qr(frame: np.ndarray) -> str | None:
    """
    Intenta decodificar un QR en el frame.
    Primera pasada: imagen en color.
    Segunda pasada: escala de grises + umbral Otsu (mejora QRs con bajo contraste).
    """
    try:
        data, _, _ = _qr_detector.detectAndDecode(frame)
    except Exception:
        data = ""
        
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
    if not REGISTRAR_DESDE_ZMQ or not DB_AVAILABLE or not cedula or not qr_data:
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
                               "hand_x": None, "hand_y": None,
                               "persona_detectada": False, "persona_x": None,
                               "persona_y": None, "persona_alto": None,
                               "persona_score": None,
                               "qr_data": None, "error": None}

            # ── 3. Procesar según modo ────────────────────────────────────
            # Los modos NO se acumulan por casualidad: cada red que se corre
            # cuesta fps. En reposo la Pi pide solo PERSONA (buscar clientes);
            # ya frente a alguien pide TODO; y durante la lectura del QR, solo QR.
            if mode in ("PERSONA", "TODO"):
                p = analizar_persona(frame)
                response["persona_detectada"] = p["detectada"]
                response["persona_x"]         = p["x"]
                response["persona_y"]         = p["y"]
                response["persona_alto"]      = p["alto"]
                response["persona_score"]     = p["score"]
                if p["detectada"]:
                    _inc("personas_detectadas")

            if mode in ("HAND", "BOTH", "TODO"):
                # Palma abierta (STOP), pulgar arriba (INTERACCIÓN) y posición
                # de la mano (para apuntar la cámara motorizada), de una pasada.
                m = analizar_mano(frame)
                response["hand_detected"] = m["palma"]
                response["thumbs_up"]     = m["pulgar"]
                response["hand_x"]        = m["x"]
                response["hand_y"]        = m["y"]
                if m["palma"]:  _inc("detecciones_mano")
                if m["pulgar"]: _inc("pulgares_arriba")

            if mode in ("QR", "BOTH", "TODO"):
                qr = decode_qr(frame)
                response["qr_data"] = qr
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
                      f"personas={s['personas_detectadas']} "
                      f"manos={s['detecciones_mano']} "
                      f"qrs={s['qr_decodificados']}")

    except KeyboardInterrupt:
        print("\n[PC-SERVER] Detenido.")
    finally:
        socket.close()
        context.term()


if __name__ == "__main__":
    run_server()
