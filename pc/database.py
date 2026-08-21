"""
=============================================================================
ROBOT BÍPEDO — Capa de Base de Datos (SQLite + SQLAlchemy)
=============================================================================
Motor    : SQLite  (archivo local, sin servidor)
ORM      : SQLAlchemy 2.x  (API moderna con Session)
Tablas   :
  clientes      — registro de cédulas con historial de visitas
  productos     — catálogo con precio y descuento
  qr_codes      — códigos QR vinculados a productos
  transacciones — log de cada interacción completa del robot

Uso rápido:
    from database import Database
    db = Database()          # Crea el archivo .db si no existe
    db.seed_from_config()    # Carga productos del config.json
    t = db.registrar_transaccion("1234567890", "QR_123", 1, 2.25, 0.25)

Dependencias:
    pip install sqlalchemy
=============================================================================
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    Boolean, DateTime, ForeignKey, UniqueConstraint, text
)
from sqlalchemy.orm import declarative_base, Session, relationship

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_DEFAULT_DB  = "robot_bipedo.db"

Base = declarative_base()


# ─────────────────────────────────────────────────────────────────────────────
# MODELOS ORM
# ─────────────────────────────────────────────────────────────────────────────

class Cliente(Base):
    """Un cliente identificado por su cédula ecuatoriana (10 dígitos)."""
    __tablename__ = "clientes"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    cedula         = Column(String(10), nullable=False, unique=True, index=True)
    primera_visita = Column(DateTime, default=datetime.utcnow, nullable=False)
    ultima_visita  = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_visitas  = Column(Integer,  default=1, nullable=False)

    transacciones  = relationship("Transaccion", back_populates="cliente_rel",
                                  cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Cliente cedula={self.cedula} visitas={self.total_visitas}>"


class Producto(Base):
    """Producto del catálogo con precio base y descuento promocional."""
    __tablename__ = "productos"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    nombre      = Column(String(120), nullable=False)
    precio_base = Column(Float,       nullable=False)
    descuento   = Column(Float,       nullable=False, default=0.0)
    activo      = Column(Boolean,     nullable=False, default=True)
    creado_at   = Column(DateTime,    default=datetime.utcnow, nullable=False)

    qr_codes      = relationship("QRCode",       back_populates="producto",
                                 cascade="all, delete-orphan")
    transacciones = relationship("Transaccion",  back_populates="producto_rel")

    @property
    def precio_final(self) -> float:
        return round(self.precio_base * (1 - self.descuento), 2)

    def __repr__(self):
        return (f"<Producto id={self.id} nombre='{self.nombre}' "
                f"base={self.precio_base} desc={int(self.descuento*100)}%>")


class QRCode(Base):
    """Código QR vinculado a un producto. Puede ser de uso único o múltiple."""
    __tablename__ = "qr_codes"
    __table_args__ = (UniqueConstraint("codigo", name="uq_qr_codigo"),)

    id          = Column(Integer, primary_key=True, autoincrement=True)
    codigo      = Column(String(512), nullable=False)      # String embebido en el QR
    producto_id = Column(Integer, ForeignKey("productos.id"), nullable=False)
    uso_unico   = Column(Boolean, default=False, nullable=False)
    usado       = Column(Boolean, default=False, nullable=False)
    creado_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    usado_at    = Column(DateTime, nullable=True)
    usos_max      = Column(Integer, default=0,    nullable=False)   # 0 = ilimitado
    usos_actuales = Column(Integer, default=0,    nullable=False)
    activo        = Column(Boolean, default=True,  nullable=False)

    producto = relationship("Producto", back_populates="qr_codes")

    def __repr__(self):
        return f"<QRCode id={self.id} prod={self.producto_id} usado={self.usado}>"


class Transaccion(Base):
    """Registro completo de una interacción robot ↔ cliente."""
    __tablename__ = "transacciones"

    id           = Column(Integer,  primary_key=True, autoincrement=True)
    cedula       = Column(String(10), ForeignKey("clientes.cedula"), nullable=False,
                          index=True)
    qr_codigo    = Column(String(512), nullable=True)       # Código leído del QR
    producto_id  = Column(Integer, ForeignKey("productos.id"), nullable=True)
    precio_base  = Column(Float,   nullable=True)
    descuento    = Column(Float,   nullable=True)
    precio_final = Column(Float,   nullable=True)
    exito        = Column(Boolean, default=True, nullable=False)  # ¿El QR fue válido?
    timestamp    = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # ¿Ya se subió esta transacción a Firebase? False = pendiente de reintento.
    sincronizado_firebase = Column(Boolean, default=False, nullable=False, index=True)

    cliente_rel  = relationship("Cliente",  back_populates="transacciones")
    producto_rel = relationship("Producto", back_populates="transacciones")

    def __repr__(self):
        return (f"<Transaccion id={self.id} cedula={self.cedula} "
                f"total=${self.precio_final} ts={self.timestamp}>")


class Encuesta(Base):
    """
    Datos que el cliente carga desde su celular en el flujo de gestos
    (PULGAR ARRIBA → estado 1). Se guarda LOCAL en la PC — funciona sin internet
    en el hotspot — y se espeja a Firebase después con sync_firebase.py.
    """
    __tablename__ = "encuestas"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    nombre    = Column(String(120), nullable=True)
    cedula    = Column(String(10),  nullable=True, index=True)
    telefono  = Column(String(30),  nullable=True)
    email     = Column(String(160), nullable=True)
    codigo    = Column(String(16),  nullable=True)   # el que ve el cliente para su muestra
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    sincronizado_firebase = Column(Boolean, default=False, nullable=False, index=True)

    def __repr__(self):
        return f"<Encuesta id={self.id} cedula={self.cedula} email={self.email}>"


# ─────────────────────────────────────────────────────────────────────────────
# CLASE PRINCIPAL DE BASE DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

class Database:
    """
    Punto de acceso centralizado a la base de datos SQLite.
    Thread-safe para uso con FastAPI y el servidor ZeroMQ corriendo en paralelo,
    gracias a check_same_thread=False y sesiones de corta vida.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = self._load_db_path_from_config()
        self._db_path = db_path
        self._engine  = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        Base.metadata.create_all(self._engine)
        self._migrar_columnas_faltantes()
        print(f"[DB] Base de datos lista: {os.path.abspath(db_path)}")

    # ── Utilidades internas ──────────────────────────────────────────────────

    def _session(self) -> Session:
        return Session(self._engine)

    def _migrar_columnas_faltantes(self):
        """
        Migración ligera para bases de datos creadas antes de agregar columnas
        nuevas. create_all() NO altera tablas existentes, así que agregamos las
        columnas que falten con ALTER TABLE. Es idempotente y seguro.
        """
        migraciones = {
            "transacciones": {
                "sincronizado_firebase": "BOOLEAN NOT NULL DEFAULT 0",
            },
            "qr_codes": {
                "usos_max":      "INTEGER NOT NULL DEFAULT 0",
                "usos_actuales": "INTEGER NOT NULL DEFAULT 0",
                "activo":        "BOOLEAN NOT NULL DEFAULT 1",
            },
        }
        with self._engine.begin() as conn:
            for tabla, columnas in migraciones.items():
                existentes = {
                    row[1] for row in conn.execute(
                        text(f"PRAGMA table_info({tabla})")
                    )
                }
                for col, definicion in columnas.items():
                    if col not in existentes:
                        conn.execute(
                            text(f"ALTER TABLE {tabla} ADD COLUMN {col} {definicion}")
                        )
                        print(f"[DB] Migración: columna '{col}' añadida a '{tabla}'.")

    @staticmethod
    def _load_db_path_from_config() -> str:
        """
        Ruta del .db. Una ruta relativa se resuelve contra la RAÍZ del repo
        (donde vive config.json), no contra el directorio actual: si no, la GUI
        lanzada desde ~ y la API lanzada desde pc/ crearían dos bases distintas
        y las ventas aparecerían "perdidas".
        """
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            ruta = cfg.get("base_de_datos", {}).get("ruta", _DEFAULT_DB)
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            ruta = _DEFAULT_DB

        p = Path(ruta).expanduser()
        if not p.is_absolute():
            p = _CONFIG_PATH.parent / p
        return str(p)

    # ── Seed inicial desde config.json ──────────────────────────────────────

    def seed_from_config(self):
        """
        Inserta en la BD los productos y QR codes definidos en config.json.
        Idempotente: no duplica si ya existen.
        """
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            print("[DB] WARN: config.json no encontrado — seed omitido.")
            return

        productos_cfg = cfg.get("productos", [])
        inserted = 0

        with self._session() as s:
            for p_cfg in productos_cfg:
                existe = s.get(Producto, p_cfg["id"])
                if existe is None:
                    prod = Producto(
                        id          = p_cfg["id"],
                        nombre      = p_cfg["nombre"],
                        precio_base = p_cfg["precio_base"],
                        descuento   = p_cfg["descuento"],
                        activo      = p_cfg.get("activo", True),
                    )
                    s.add(prod)

                    qr = QRCode(
                        codigo      = p_cfg["qr_codigo"],
                        producto_id = p_cfg["id"],
                        uso_unico   = False,
                    )
                    s.add(qr)
                    inserted += 1

            s.commit()

        print(f"[DB] Seed completado: {inserted} productos nuevos insertados.")

    # ── CRUD Clientes ────────────────────────────────────────────────────────

    def get_or_create_cliente(self, cedula: str) -> tuple[Cliente, bool]:
        """
        Devuelve (cliente, creado: bool).
        Si ya existe, actualiza ultima_visita y total_visitas.
        """
        with self._session() as s:
            cliente = s.query(Cliente).filter_by(cedula=cedula).first()
            if cliente:
                cliente.ultima_visita = datetime.utcnow()
                cliente.total_visitas += 1
                s.commit()
                s.refresh(cliente)
                return cliente, False
            else:
                nuevo = Cliente(cedula=cedula)
                s.add(nuevo)
                s.commit()
                s.refresh(nuevo)
                return nuevo, True

    def get_clientes(self, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._session() as s:
            rows = s.query(Cliente).order_by(
                Cliente.ultima_visita.desc()
            ).offset(offset).limit(limit).all()
            return [self._cliente_to_dict(r) for r in rows]

    def get_cliente_by_cedula(self, cedula: str) -> Optional[dict]:
        with self._session() as s:
            row = s.query(Cliente).filter_by(cedula=cedula).first()
            return self._cliente_to_dict(row) if row else None

    # ── CRUD Productos ───────────────────────────────────────────────────────

    def get_productos(self, solo_activos: bool = False) -> list[dict]:
        with self._session() as s:
            q = s.query(Producto)
            if solo_activos:
                q = q.filter_by(activo=True)
            rows = q.order_by(Producto.id).all()
            return [self._producto_to_dict(r) for r in rows]

    def get_producto_by_id(self, producto_id: int) -> Optional[dict]:
        with self._session() as s:
            row = s.get(Producto, producto_id)
            return self._producto_to_dict(row) if row else None

    def crear_producto(self, nombre: str, precio_base: float,
                       descuento: float, activo: bool = True) -> dict:
        with self._session() as s:
            prod = Producto(nombre=nombre, precio_base=precio_base,
                            descuento=descuento, activo=activo)
            s.add(prod)
            s.commit()
            s.refresh(prod)
            return self._producto_to_dict(prod)

    def actualizar_producto(self, producto_id: int, **kwargs) -> Optional[dict]:
        campos_validos = {"nombre", "precio_base", "descuento", "activo"}
        with self._session() as s:
            prod = s.get(Producto, producto_id)
            if not prod:
                return None
            for k, v in kwargs.items():
                if k in campos_validos:
                    setattr(prod, k, v)
            s.commit()
            s.refresh(prod)
            return self._producto_to_dict(prod)

    # ── CRUD QR Codes ────────────────────────────────────────────────────────

    def get_qr_codes(self, solo_disponibles: bool = False) -> list[dict]:
        with self._session() as s:
            q = s.query(QRCode)
            if solo_disponibles:
                q = q.filter_by(usado=False)
            rows = q.order_by(QRCode.id).all()
            return [self._qr_to_dict(r) for r in rows]

    def crear_qr(self, codigo: str, producto_id: int,
                 uso_unico: bool = False, usos_max: int = 0) -> Optional[dict]:
        with self._session() as s:
            qr = QRCode(codigo=codigo, producto_id=producto_id,
                        uso_unico=uso_unico, usos_max=usos_max)
            s.add(qr)
            s.commit()
            s.refresh(qr)
            return self._qr_to_dict(qr)

    def desactivar_qr(self, qr_id: int) -> bool:
        with self._session() as s:
            qr = s.get(QRCode, qr_id)
            if qr is None:
                return False
            qr.activo = False
            s.commit()
            return True

    def get_qr_activos_detallado(self, limit: int = 100) -> list[dict]:
        """QR activos con nombre y descuento del producto (para el gestor del dashboard)."""
        with self._session() as s:
            rows = (s.query(QRCode)
                     .filter_by(activo=True)
                     .order_by(QRCode.id.desc())
                     .limit(limit).all())
            salida = []
            for q in rows:
                d = self._qr_to_dict(q)
                p = q.producto
                d["producto_nombre"] = p.nombre if p else None
                d["descuento_pct"]   = int(p.descuento * 100) if p else 0
                salida.append(d)
            return salida

    def validar_y_usar_qr(self, codigo: str) -> Optional[dict]:
        """
        Valida si el código QR existe y está disponible.
        Si es de uso único, lo marca como usado.
        Devuelve el QR con su producto, o None si no es válido.
        """
        with self._session() as s:
            qr = s.query(QRCode).filter_by(codigo=codigo).first()
            if not qr or not qr.activo:
                return None
            if qr.uso_unico and qr.usado:
                return None    # QR de uso único ya consumido
            if qr.usos_max and qr.usos_actuales >= qr.usos_max:
                return None    # límite de usos alcanzado
            qr.usos_actuales += 1
            qr.usado_at = datetime.utcnow()
            if qr.uso_unico or (qr.usos_max and qr.usos_actuales >= qr.usos_max):
                qr.usado = True
            s.commit()
            return self._qr_to_dict(qr)

    def consumir_qr_promo(self, codigo: str) -> Optional[dict]:
        """
        Para el ROBOT: valida el código leído por la cámara, consume un uso y
        devuelve la promo real (nombre del producto + descuento). None si no
        existe, está inactivo o se agotó el límite de usos.
        """
        with self._session() as s:
            qr = s.query(QRCode).filter_by(codigo=codigo).first()
            if not qr or not qr.activo:
                return None
            if qr.usos_max and qr.usos_actuales >= qr.usos_max:
                return None
            qr.usos_actuales += 1
            qr.usado_at = datetime.utcnow()
            if qr.uso_unico or (qr.usos_max and qr.usos_actuales >= qr.usos_max):
                qr.usado = True
            p = qr.producto
            resultado = {
                "valido":        True,
                "nombre":        p.nombre if p else None,
                "descuento_pct": int(p.descuento * 100) if p else 0,
                "usos_actuales": qr.usos_actuales,
                "usos_max":      qr.usos_max,
            }
            s.commit()
            return resultado

    # ── CRUD Transacciones ───────────────────────────────────────────────────

    def registrar_transaccion(self, cedula: str, qr_codigo: Optional[str],
                               producto_id: Optional[int],
                               precio_base: Optional[float],
                               descuento: Optional[float],
                               exito: bool = True) -> dict:
        """
        Registra una interacción completa. Crea el cliente si no existe.
        """
        self.get_or_create_cliente(cedula)     # Actualiza historial del cliente

        precio_final = None
        if precio_base is not None and descuento is not None:
            precio_final = round(precio_base * (1 - descuento), 2)

        with self._session() as s:
            t = Transaccion(
                cedula       = cedula,
                qr_codigo    = qr_codigo,
                producto_id  = producto_id,
                precio_base  = precio_base,
                descuento    = descuento,
                precio_final = precio_final,
                exito        = exito,
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            return self._transaccion_to_dict(t)

    def get_transacciones(self, limit: int = 50, offset: int = 0,
                          cedula: Optional[str] = None) -> list[dict]:
        with self._session() as s:
            q = s.query(Transaccion)
            if cedula:
                q = q.filter_by(cedula=cedula)
            rows = q.order_by(
                Transaccion.timestamp.desc()
            ).offset(offset).limit(limit).all()
            return [self._transaccion_to_dict(r) for r in rows]

    # ── CRUD Encuestas (flujo de gestos: pulgar arriba) ──────────────────────

    def registrar_encuesta(self, nombre: Optional[str], cedula: Optional[str],
                           telefono: Optional[str], email: Optional[str],
                           codigo: Optional[str] = None) -> dict:
        """Guarda los datos que el cliente cargó desde su celular."""
        with self._session() as s:
            e = Encuesta(nombre=nombre, cedula=cedula, telefono=telefono,
                         email=email, codigo=codigo)
            s.add(e)
            s.commit()
            s.refresh(e)
            return self._encuesta_to_dict(e)

    def get_encuestas(self, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._session() as s:
            rows = (s.query(Encuesta)
                     .order_by(Encuesta.timestamp.desc())
                     .offset(offset).limit(limit).all())
            return [self._encuesta_to_dict(r) for r in rows]

    def get_encuestas_pendientes_firebase(self, limit: int = 500) -> list[dict]:
        """Encuestas que todavía no se subieron a Firebase (para sync_firebase.py)."""
        with self._session() as s:
            rows = (s.query(Encuesta)
                     .filter_by(sincronizado_firebase=False)
                     .order_by(Encuesta.timestamp.asc())
                     .limit(limit).all())
            return [self._encuesta_to_dict(r) for r in rows]

    def marcar_encuesta_sincronizada(self, encuesta_id: int) -> bool:
        with self._session() as s:
            e = s.get(Encuesta, encuesta_id)
            if e is None:
                return False
            e.sincronizado_firebase = True
            s.commit()
            return True

    # ── Sincronización con Firebase ──────────────────────────────────────────

    def marcar_sincronizado(self, transaccion_id: int) -> bool:
        """Marca una transacción como ya subida a Firebase."""
        with self._session() as s:
            t = s.get(Transaccion, transaccion_id)
            if t is None:
                return False
            t.sincronizado_firebase = True
            s.commit()
            return True

    def get_pendientes_firebase(self, limit: int = 500) -> list[dict]:
        """
        Devuelve las transacciones que todavía no se subieron a Firebase
        (por falta de internet u otro fallo). Usado por sync_firebase.py.
        """
        with self._session() as s:
            rows = (s.query(Transaccion)
                     .filter_by(sincronizado_firebase=False)
                     .order_by(Transaccion.timestamp.asc())
                     .limit(limit).all())
            return [self._transaccion_to_dict(r) for r in rows]

    # ── Estadísticas para el dashboard ──────────────────────────────────────

    def get_stats(self) -> dict:
        with self._session() as s:
            total_clientes      = s.query(Cliente).count()
            total_transacciones = s.query(Transaccion).count()
            trans_exitosas      = s.query(Transaccion).filter_by(exito=True).count()
            total_productos     = s.query(Producto).filter_by(activo=True).count()
            total_qr_activos    = s.query(QRCode).filter_by(usado=False).count()

            # Ingresos totales (suma de precio_final de transacciones exitosas)
            result = s.execute(
                text("SELECT COALESCE(SUM(precio_final), 0) FROM transacciones "
                     "WHERE exito = 1 AND precio_final IS NOT NULL")
            ).scalar()
            ingresos_totales = round(float(result), 2)

            # Transacciones de hoy
            hoy = datetime.utcnow().date().isoformat()
            trans_hoy = s.execute(
                text("SELECT COUNT(*) FROM transacciones "
                     f"WHERE DATE(timestamp) = '{hoy}'")
            ).scalar()

        return {
            "total_clientes":       total_clientes,
            "total_transacciones":  total_transacciones,
            "transacciones_exitosas": trans_exitosas,
            "tasa_exito_pct":       round(trans_exitosas / max(total_transacciones, 1) * 100, 1),
            "productos_activos":    total_productos,
            "qr_disponibles":       total_qr_activos,
            "ingresos_totales_usd": ingresos_totales,
            "transacciones_hoy":    int(trans_hoy),
        }

    # ── Serializadores ───────────────────────────────────────────────────────

    @staticmethod
    def _cliente_to_dict(c: Cliente) -> dict:
        return {
            "id":             c.id,
            "cedula":         c.cedula,
            "primera_visita": c.primera_visita.isoformat(),
            "ultima_visita":  c.ultima_visita.isoformat(),
            "total_visitas":  c.total_visitas,
        }

    @staticmethod
    def _producto_to_dict(p: Producto) -> dict:
        return {
            "id":           p.id,
            "nombre":       p.nombre,
            "precio_base":  p.precio_base,
            "descuento":    p.descuento,
            "descuento_pct": int(p.descuento * 100),
            "precio_final": p.precio_final,
            "activo":       p.activo,
            "creado_at":    p.creado_at.isoformat(),
        }

    @staticmethod
    def _qr_to_dict(q: QRCode) -> dict:
        return {
            "id":          q.id,
            "codigo":      q.codigo,
            "producto_id": q.producto_id,
            "uso_unico":   q.uso_unico,
            "usado":       q.usado,
            "usos_max":       q.usos_max,
            "usos_actuales":  q.usos_actuales,
            "activo":         q.activo,
            "creado_at":   q.creado_at.isoformat(),
            "usado_at":    q.usado_at.isoformat() if q.usado_at else None,
        }

    @staticmethod
    def _transaccion_to_dict(t: Transaccion) -> dict:
        return {
            "id":           t.id,
            "cedula":       t.cedula,
            "qr_codigo":    t.qr_codigo,
            "producto_id":  t.producto_id,
            "precio_base":  t.precio_base,
            "descuento":    t.descuento,
            "precio_final": t.precio_final,
            "exito":        t.exito,
            "timestamp":    t.timestamp.isoformat(),
            "sincronizado_firebase": t.sincronizado_firebase,
        }

    @staticmethod
    def _encuesta_to_dict(e: "Encuesta") -> dict:
        return {
            "id":        e.id,
            "nombre":    e.nombre,
            "cedula":    e.cedula,
            "telefono":  e.telefono,
            "email":     e.email,
            "codigo":    e.codigo,
            "timestamp": e.timestamp.isoformat(),
            "sincronizado_firebase": e.sincronizado_firebase,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Instancia global compartida (importar en los demás módulos)
# ─────────────────────────────────────────────────────────────────────────────
db = Database()
# --- fin del módulo database.py ---
