"""
=============================================================================
ROBOT BÍPEDO — API REST + Dashboard (FastAPI)
=============================================================================
Corre en la PC como proceso independiente al servidor ZeroMQ.
Expone:
  - Endpoints REST JSON para CRUD de todas las tablas
  - GET /         → Sirve el dashboard.html
  - GET /stats    → Estadísticas en tiempo real (BD + servidor ZeroMQ)

Ejecución:
    pip install fastapi uvicorn sqlalchemy
    python api_server.py
    # → http://localhost:8000 para el dashboard
    # → http://localhost:8000/docs para Swagger UI

Endpoints principales:
  GET  /api/stats
  GET  /api/clientes          ?limit=50&offset=0
  GET  /api/clientes/{cedula}
  GET  /api/productos         ?solo_activos=false
  POST /api/productos
  PUT  /api/productos/{id}
  GET  /api/qr                ?solo_disponibles=false
  POST /api/qr
  GET  /api/transacciones     ?limit=50&cedula=optional
  POST /api/transacciones/manual
  GET  /api/server/stats      → estadísticas vivas del servidor ZeroMQ
=============================================================================
"""

import json
import sys
import uvicorn
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ──────────────────────────────────────────────
# IMPORTS LOCALES
# ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from database import db
from sesion import store as sesiones, Fase, validar_cedula

# Firebase (espejo en la nube, opcional y resiliente)
try:
    from firebase_client import fb as _fb
    FIREBASE_AVAILABLE = True
except Exception as _exc:   # noqa: BLE001 — nunca debe tumbar la API
    FIREBASE_AVAILABLE = False
    _fb = None
    print(f"[API] WARN: firebase_client no disponible — {_exc}")

# Intentar importar stats vivas del servidor ZeroMQ (si corre en el mismo proceso)
try:
    from pc_server import get_live_stats, set_active_cedula
    ZMQSERVER_IMPORTED = True
except ImportError:
    ZMQSERVER_IMPORTED = False
    def get_live_stats():
        return {"info": "Servidor ZeroMQ corriendo en proceso separado."}

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
_CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def _get_api_port() -> int:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get("red", {}).get("api_puerto", 8000)
    except FileNotFoundError:
        return 8000

API_PORT    = _get_api_port()
STATIC_DIR  = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────
# APP FASTAPI
# ──────────────────────────────────────────────
app = FastAPI(
    title       = "Robot Bípedo — Panel de Administración",
    description = "API REST para gestionar clientes, productos, QR y transacciones.",
    version     = "1.0.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

# Servir archivos estáticos (dashboard.html, etc.)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─────────────────────────────────────────────────────────────────────────────
# MODELOS PYDANTIC (validación de entrada)
# ─────────────────────────────────────────────────────────────────────────────

class ProductoCreate(BaseModel):
    nombre:      str   = Field(..., min_length=2, max_length=120,
                                example="Gaseosa 500ml")
    precio_base: float = Field(..., gt=0, example=1.50)
    descuento:   float = Field(..., ge=0, le=1, example=0.20,
                                description="Valor entre 0.0 y 1.0")
    activo:      bool  = Field(True)

class ProductoUpdate(BaseModel):
    nombre:      Optional[str]   = None
    precio_base: Optional[float] = Field(None, gt=0)
    descuento:   Optional[float] = Field(None, ge=0, le=1)
    activo:      Optional[bool]  = None

class QRCreate(BaseModel):
    codigo:      str  = Field(..., min_length=1, example="PROD:Gaseosa 500ml|DESC:0.20|BASE:1.50")
    producto_id: int  = Field(..., gt=0, example=1)
    uso_unico:   bool = Field(False)

class QRGenerar(BaseModel):
    """Alta rápida desde el dashboard: crea el producto + su QR de descuento."""
    nombre:        str = Field(..., min_length=2, max_length=120, example="Gaseosa")
    descuento_pct: float = Field(..., ge=0, le=100, example=20)
    limite_usos:   int = Field(0, ge=0, example=100, description="0 = ilimitado")

class TransaccionManual(BaseModel):
    cedula:      str            = Field(..., min_length=10, max_length=10,
                                         example="1234567890")
    qr_codigo:   Optional[str] = None
    producto_id: Optional[int] = None
    precio_base: Optional[float] = None
    descuento:   Optional[float] = None
    exito:       bool           = Field(True)

class CedulaActiva(BaseModel):
    cedula: str = Field(..., min_length=10, max_length=10)

class RegistrarVenta(BaseModel):
    """Venta completa que envía la Raspberry al terminar el flujo."""
    cedula:    str = Field(..., min_length=10, max_length=10, example="1234567890")
    qr_codigo: str = Field(..., example="PROD:Gaseosa 500ml|DESC:0.20|BASE:1.50")


# ── Modelos del flujo nuevo: el celular como control ─────────────────────────

class SesionCedula(BaseModel):
    cedula: str = Field(..., min_length=10, max_length=10, example="1710034065")

class SesionControl(BaseModel):
    """Consigna del joystick. v = adelante/atrás, w = giro. Ambos en [-1, 1]."""
    v: float = Field(0.0, ge=-1.0, le=1.0)
    w: float = Field(0.0, ge=-1.0, le=1.0)

class SesionAccion(BaseModel):
    accion: str  = Field(..., example="BAILE")
    datos:  dict = Field(default_factory=dict)

class SesionProducto(BaseModel):
    producto_id: int = Field(..., gt=0)

class SesionFinalizar(BaseModel):
    producto_id: Optional[int] = None


class EncuestaCreate(BaseModel):
    """Datos que el cliente carga desde su celular (estado 1: pulgar arriba)."""
    nombre:   str = Field(..., min_length=2, max_length=120, example="Ana Pérez")
    cedula:   str = Field(..., min_length=10, max_length=10, example="1710034065")
    telefono: str = Field("", max_length=30, example="0991234567")
    email:    str = Field("", max_length=160, example="ana@mail.com")


# ─────────────────────────────────────────────────────────────────────────────
# RUTAS — Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, tags=["Dashboard"],
         summary="Panel de administración")
async def dashboard():
    """Sirve el dashboard HTML. Acceder desde el navegador."""
    html_path = STATIC_DIR / "dashboard.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    return HTMLResponse(content="<h2>dashboard.html no encontrado en /static/</h2>",
                        status_code=404)


# ─────────────────────────────────────────────────────────────────────────────
# RUTAS — Encuesta (estado 1: el cliente carga sus datos desde el celular)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/encuesta", response_class=HTMLResponse, tags=["Encuesta"],
         summary="Formulario de datos del cliente")
async def servir_encuesta():
    """Sirve el formulario que el cliente abre al escanear el QR del estado 1."""
    html_path = STATIC_DIR / "encuesta.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    return HTMLResponse("<h2>encuesta.html no encontrado en pc/static/</h2>",
                        status_code=404)


@app.post("/api/encuesta", status_code=201, tags=["Encuesta"],
          summary="Registrar los datos del cliente")
async def registrar_encuesta(datos: EncuestaCreate):
    """
    Guarda los datos en SQLite (fuente de verdad, funciona SIN internet) y
    devuelve un código para retirar la muestra. El espejo a Firebase se hace
    después con sync_firebase.py, igual que las ventas.
    """
    ok, motivo = validar_cedula(datos.cedula)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Cédula inválida: {motivo}")

    import random
    codigo = "RM-" + "".join(
        random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
    reg = db.registrar_encuesta(
        nombre   = datos.nombre.strip(),
        cedula   = datos.cedula.strip(),
        telefono = datos.telefono.strip(),
        email    = datos.email.strip(),
        codigo   = codigo,
    )
    return {"ok": True, "id": reg["id"], "codigo": codigo}


@app.get("/api/encuestas", tags=["Encuesta"], summary="Listar encuestas cargadas")
async def listar_encuestas(limit: int = Query(100, ge=1, le=500),
                           offset: int = Query(0, ge=0)):
    return db.get_encuestas(limit=limit, offset=offset)


# ─────────────────────────────────────────────────────────────────────────────
# RUTAS — Estadísticas
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/stats", tags=["Estadísticas"],
         summary="Estadísticas globales de la BD")
async def get_stats():
    return db.get_stats()

@app.get("/api/server/stats", tags=["Estadísticas"],
         summary="Estadísticas en vivo del servidor ZeroMQ")
async def get_server_stats():
    return get_live_stats()


# ─────────────────────────────────────────────────────────────────────────────
# RUTAS — Clientes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/clientes", tags=["Clientes"])
async def listar_clientes(
    limit:  int = Query(50,  ge=1, le=500),
    offset: int = Query(0,   ge=0),
):
    return db.get_clientes(limit=limit, offset=offset)

@app.get("/api/clientes/{cedula}", tags=["Clientes"])
async def obtener_cliente(cedula: str):
    cliente = db.get_cliente_by_cedula(cedula)
    if not cliente:
        raise HTTPException(404, f"Cliente con cédula '{cedula}' no encontrado.")
    return cliente

@app.get("/api/clientes/{cedula}/transacciones", tags=["Clientes"])
async def transacciones_de_cliente(
    cedula: str,
    limit:  int = Query(20, ge=1, le=200),
):
    return db.get_transacciones(limit=limit, cedula=cedula)


# ─────────────────────────────────────────────────────────────────────────────
# RUTAS — Productos
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/productos", tags=["Productos"])
async def listar_productos(solo_activos: bool = False):
    return db.get_productos(solo_activos=solo_activos)

@app.get("/api/productos/{producto_id}", tags=["Productos"])
async def obtener_producto(producto_id: int):
    prod = db.get_producto_by_id(producto_id)
    if not prod:
        raise HTTPException(404, f"Producto {producto_id} no encontrado.")
    return prod

@app.post("/api/productos", status_code=201, tags=["Productos"])
async def crear_producto(body: ProductoCreate):
    return db.crear_producto(
        nombre      = body.nombre,
        precio_base = body.precio_base,
        descuento   = body.descuento,
        activo      = body.activo,
    )

@app.put("/api/productos/{producto_id}", tags=["Productos"])
async def actualizar_producto(producto_id: int, body: ProductoUpdate):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No se enviaron campos para actualizar.")
    prod = db.actualizar_producto(producto_id, **updates)
    if not prod:
        raise HTTPException(404, f"Producto {producto_id} no encontrado.")
    return prod


# ─────────────────────────────────────────────────────────────────────────────
# RUTAS — QR Codes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/qr", tags=["QR Codes"])
async def listar_qr(solo_disponibles: bool = False):
    return db.get_qr_codes(solo_disponibles=solo_disponibles)

@app.post("/api/qr", status_code=201, tags=["QR Codes"])
async def crear_qr(body: QRCreate):
    qr = db.crear_qr(
        codigo      = body.codigo,
        producto_id = body.producto_id,
        uso_unico   = body.uso_unico,
    )
    if not qr:
        raise HTTPException(400, "Error al crear el QR.")
    return qr

@app.post("/api/qr/validar", tags=["QR Codes"],
          summary="Validar un QR sin registrar transacción")
async def validar_qr(body: dict):
    codigo = body.get("codigo", "")
    if not codigo:
        raise HTTPException(400, "Campo 'codigo' requerido.")
    resultado = db.validar_y_usar_qr(codigo)
    return {"valido": resultado is not None, "qr": resultado}


@app.post("/api/qr/generar", status_code=201, tags=["QR Codes"],
          summary="Crear producto + QR de descuento (para el gestor del dashboard)")
async def generar_qr(body: QRGenerar):
    """
    Un solo paso desde el dashboard: crea el producto con su descuento y le arma
    un QR con límite de usos. Devuelve el código y la URL de su imagen.
    """
    prod = db.crear_producto(
        nombre      = body.nombre.strip(),
        precio_base = 0.0,                       # promo de descuento, sin precio base
        descuento   = round(body.descuento_pct / 100.0, 4),
        activo      = True,
    )
    codigo = f"PROMO:{body.nombre.strip()}|DESC:{int(body.descuento_pct)}|ID:{prod['id']}"
    qr = db.crear_qr(codigo=codigo, producto_id=prod["id"],
                     uso_unico=False, usos_max=int(body.limite_usos))
    if not qr:
        raise HTTPException(400, "No se pudo crear el QR.")
    return {"ok": True, "id": qr["id"], "codigo": codigo,
            "imagen_url": f"/api/qr/imagen?texto={codigo}"}


@app.get("/api/qr/activos", tags=["QR Codes"],
         summary="QR activos con nombre y descuento del producto")
async def qr_activos(limit: int = Query(100, ge=1, le=500)):
    return db.get_qr_activos_detallado(limit=limit)


@app.get("/api/qr/promo", tags=["QR Codes"],
         summary="El robot lee un QR y recibe su promo (consume un uso)")
async def qr_promo(codigo: str = Query(..., min_length=1)):
    r = db.consumir_qr_promo(codigo)
    return r if r is not None else {"valido": False}


@app.post("/api/qr/{qr_id}/desactivar", tags=["QR Codes"],
          summary="Desactivar un QR")
async def desactivar_qr(qr_id: int):
    if not db.desactivar_qr(qr_id):
        raise HTTPException(404, f"QR {qr_id} no encontrado.")
    return {"ok": True, "id": qr_id}


@app.get("/api/qr/imagen", tags=["QR Codes"],
         summary="Imagen SVG de un QR para mostrar o imprimir")
async def imagen_qr(texto: str = Query(..., min_length=1)):
    """
    Devuelve el QR de 'texto' como imagen SVG (crece sin pixelarse al imprimir y
    NO necesita Pillow). Se usa desde el dashboard: <img src=".../api/qr/imagen?texto=...">.
    """
    try:
        import io
        import qrcode
        import qrcode.image.svg
    except ImportError:
        raise HTTPException(501, "Falta la librería qrcode en la PC: pip install qrcode")

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=4)
    qr.add_data(texto)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


# ─────────────────────────────────────────────────────────────────────────────
# RUTAS — Transacciones
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/transacciones", tags=["Transacciones"])
async def listar_transacciones(
    limit:  int           = Query(50,  ge=1, le=500),
    offset: int           = Query(0,   ge=0),
    cedula: Optional[str] = Query(None),
):
    return db.get_transacciones(limit=limit, offset=offset, cedula=cedula)

def _mirror_a_firebase(trans: dict):
    """Sube la transacción a Firebase (si está activo) y la marca sincronizada."""
    if FIREBASE_AVAILABLE and _fb is not None and _fb.esta_disponible():
        if _fb.guardar_transaccion(trans):
            db.marcar_sincronizado(trans["id"])
            print(f"[FIREBASE] Transacción {trans['id']} subida a la nube.")
        else:
            print(f"[FIREBASE] Transacción {trans['id']} quedó pendiente de subir.")


@app.post("/api/transacciones", status_code=201, tags=["Transacciones"],
          summary="Registrar una venta completa (la envía la Raspberry)")
async def registrar_venta(body: RegistrarVenta):
    """
    Punto de entrada real desde el robot: recibe cédula + código QR, valida el
    QR contra la BD, calcula precio/descuento, registra la transacción en SQLite
    y la espeja en Firebase. Devuelve la transacción registrada.
    """
    qr_info = db.validar_y_usar_qr(body.qr_codigo)
    if qr_info:
        prod = db.get_producto_by_id(qr_info["producto_id"])
        t = db.registrar_transaccion(
            cedula      = body.cedula,
            qr_codigo   = body.qr_codigo,
            producto_id = qr_info["producto_id"],
            precio_base = prod["precio_base"] if prod else None,
            descuento   = prod["descuento"]   if prod else None,
            exito       = True,
        )
        print(f"[DB] Venta registrada: cédula={body.cedula} "
              f"producto={prod['nombre'] if prod else 'N/A'}")
    else:
        # QR no válido o ya usado: se registra como intento fallido
        t = db.registrar_transaccion(
            cedula      = body.cedula,
            qr_codigo   = body.qr_codigo,
            producto_id = None,
            precio_base = None,
            descuento   = None,
            exito       = False,
        )
        print(f"[DB] QR inválido registrado: cédula={body.cedula}")

    _mirror_a_firebase(t)
    return t


@app.post("/api/transacciones/manual", status_code=201, tags=["Transacciones"],
          summary="Registrar manualmente una transacción (testing/corrección)")
async def registrar_transaccion_manual(body: TransaccionManual):
    t = db.registrar_transaccion(
        cedula      = body.cedula,
        qr_codigo   = body.qr_codigo,
        producto_id = body.producto_id,
        precio_base = body.precio_base,
        descuento   = body.descuento,
        exito       = body.exito,
    )
    _mirror_a_firebase(t)
    return t

# Endpoint para que la Pi informe la cédula activa al servidor ZeroMQ
@app.post("/api/sesion/cedula", tags=["Sesión"],
          summary="Informar cédula activa al servidor ZeroMQ")
async def set_cedula_activa(body: CedulaActiva):
    if ZMQSERVER_IMPORTED:
        set_active_cedula(body.cedula)
        return {"ok": True, "cedula": body.cedula}
    return {"ok": False, "info": "Servidor ZeroMQ en proceso separado — no aplica."}


# ─────────────────────────────────────────────────────────────────────────────
# RUTAS — SESIÓN INTERACTIVA (el celular del cliente como control)
#
# Este bloque es "la base de datos" que consulta el robot. El celular escribe
# acá; la Raspberry lee acá 20 veces por segundo. Todo pasa por el hotspot, sin
# salir a internet: por eso el robot responde al instante cuando el cliente
# suelta el acelerador.
#
# Flujo:
#   Pi     → POST /api/sesion/nueva          → dibuja el QR con la URL devuelta
#   Celular→ GET  /c/{token}                 → abre la WebApp
#   Celular→ POST /api/sesion/{token}/cedula → valida Módulo 10, gana el control
#   Celular→ POST /api/sesion/{token}/control (joystick, ~10 Hz)
#   Pi     → GET  /api/sesion/{token}/estado (20 Hz)
#   Celular→ POST /api/sesion/{token}/finalizar → registra la venta, libera todo
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/sesion/nueva", tags=["Sesión"],
          summary="Abrir una sesión y obtener la URL para el QR (la llama la Pi)")
async def sesion_nueva(request: Request):
    """
    La URL se arma con el Host con el que la Pi llegó hasta acá, no con una IP
    de configuración. Es a propósito: la IP de la PC en el hotspot cambia cada
    vez que se reinicia, y así el QR apunta siempre a una dirección que ya se
    demostró alcanzable — la que la Pi acaba de usar para pedir la sesión.
    """
    s = sesiones.crear()
    host = request.headers.get("host") or f"localhost:{API_PORT}"
    url  = f"{request.url.scheme}://{host}/c/{s.token}"
    print(f"[SESION] Nueva sesión {s.token} → {url}")
    return {"token": s.token, "url": url, "seq": s.seq, "fase": s.fase}


@app.get("/c/{token}", response_class=HTMLResponse, tags=["Sesión"],
         summary="WebApp del cliente (la abre el celular al escanear el QR)")
async def sesion_webapp(token: str):
    """
    Sirve la WebApp. El token va en la ruta y el JavaScript lo saca de ahí.

    La página es 100% autocontenida (sin CDN, sin fuentes ni scripts externos)
    porque el celular está en un hotspot que normalmente NO tiene internet.
    """
    html_path = STATIC_DIR / "control.html"
    if not html_path.exists():
        return HTMLResponse("<h2>control.html no encontrado en pc/static/</h2>",
                            status_code=404)
    sesiones.marcar_conectado(token)
    return FileResponse(str(html_path))


@app.get("/api/sesion/{token}", tags=["Sesión"],
         summary="Estado de la sesión (lo consulta el celular)")
async def sesion_ver(token: str):
    s = sesiones.obtener(token)
    if not s:
        raise HTTPException(404, "Sesión no encontrada o vencida.")
    d = s.a_dict()
    if s.producto_id:
        d["producto"] = db.get_producto_by_id(s.producto_id)
    return d


@app.post("/api/sesion/{token}/cedula", tags=["Sesión"],
          summary="El cliente ingresa su cédula desde el celular")
async def sesion_cedula(token: str, body: SesionCedula):
    ok, motivo, s = sesiones.set_cedula(token, body.cedula)
    if s is None:
        raise HTTPException(404, motivo)
    if not ok:
        # 200 con ok=False a propósito: es un error de dedo del cliente, no un
        # fallo del sistema. La WebApp muestra el motivo y lo deja reintentar.
        return {"ok": False, "motivo": motivo}

    # Se le avisa también al servidor de visión, que ya tenía este mecanismo.
    if ZMQSERVER_IMPORTED:
        set_active_cedula(body.cedula)

    print(f"[SESION] {token}: cédula validada.")
    return {"ok": True, "fase": s.fase, "seq": s.seq}


@app.post("/api/sesion/{token}/control", tags=["Sesión"],
          summary="Joystick: consigna de movimiento (la manda el celular ~10 Hz)")
async def sesion_control(token: str, body: SesionControl):
    if not sesiones.set_control(token, body.v, body.w):
        raise HTTPException(409, "La sesión no está habilitada para manejar.")
    return {"ok": True}


@app.post("/api/sesion/{token}/accion", tags=["Sesión"],
          summary="Acción discreta desde el celular (BAILE, SALUDO, ...)")
async def sesion_accion(token: str, body: SesionAccion):
    if not sesiones.push_accion(token, body.accion.upper(), body.datos):
        raise HTTPException(409, "La sesión no acepta acciones en este momento.")
    return {"ok": True}


@app.post("/api/sesion/{token}/producto", tags=["Sesión"],
          summary="El cliente elige un producto del catálogo")
async def sesion_producto(token: str, body: SesionProducto):
    prod = db.get_producto_by_id(body.producto_id)
    if not prod:
        raise HTTPException(404, "Producto no encontrado.")
    if not sesiones.set_producto(token, body.producto_id):
        raise HTTPException(409, "La sesión no está habilitada.")
    return {"ok": True, "producto": prod}


@app.post("/api/sesion/{token}/finalizar", tags=["Sesión"],
          summary="Cierra la compra, la registra y libera el control del robot")
async def sesion_finalizar(token: str, body: SesionFinalizar):
    s = sesiones.obtener(token)
    if not s:
        raise HTTPException(404, "Sesión no encontrada o vencida.")
    if not s.cedula:
        raise HTTPException(400, "La sesión no tiene una cédula validada.")

    producto_id = body.producto_id or s.producto_id
    if not producto_id:
        raise HTTPException(400, "No se eligió ningún producto.")

    prod = db.get_producto_by_id(producto_id)
    if not prod:
        raise HTTPException(404, "Producto no encontrado.")

    # El QR impreso no interviene en este flujo: el producto se elige en el
    # celular. Se deja marcado el origen para poder distinguir en el dashboard
    # las ventas hechas por la WebApp de las del QR de papel.
    t = db.registrar_transaccion(
        cedula      = s.cedula,
        qr_codigo   = f"WEBAPP:{producto_id}",
        producto_id = producto_id,
        precio_base = prod["precio_base"],
        descuento   = prod["descuento"],
        exito       = True,
    )
    _mirror_a_firebase(t)
    sesiones.finalizar(token, t, mensaje="¡Gracias por tu compra!")
    print(f"[SESION] {token}: venta registrada "
          f"(cédula={s.cedula} producto={prod['nombre']}).")

    return {"ok": True, "transaccion": t, "producto": prod}


@app.post("/api/sesion/{token}/cancelar", tags=["Sesión"],
          summary="El cliente se va sin comprar")
async def sesion_cancelar(token: str):
    s = sesiones.cancelar(token, "El cliente cerró la sesión.")
    if not s:
        raise HTTPException(404, "Sesión no encontrada.")
    return {"ok": True, "fase": s.fase}


@app.get("/api/sesion/{token}/estado", tags=["Sesión"],
         summary="Lo que consulta la Raspberry 20 veces por segundo")
async def sesion_estado(token: str, since: int = Query(-1)):
    """
    `since` es el último `seq` que vio la Pi. La respuesta trae `cambio=true`
    solo si algo se movió, y en ese caso también las acciones pendientes — que
    se entregan UNA sola vez.

    El bloque `control` viene siempre, cambie o no `seq`: el joystick se
    actualiza diez veces por segundo y no tendría sentido que cada movimiento
    del dedo contara como "un cambio".
    """
    d = sesiones.estado(token, desde_seq=since)
    if d is None:
        raise HTTPException(404, "Sesión no encontrada o vencida.")
    return d


# ─────────────────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[API] Iniciando en http://localhost:{API_PORT}")
    print(f"[API] Swagger UI: http://localhost:{API_PORT}/docs")
    uvicorn.run(
        "api_server:app",
        host    = "0.0.0.0",
        port    = API_PORT,
        reload  = False,
        workers = 1,
    )
