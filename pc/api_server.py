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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ──────────────────────────────────────────────
# IMPORTS LOCALES
# ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from database import db

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
