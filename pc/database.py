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

    cliente_rel  = relationship("Cliente",  back_populates="transacciones")
    producto_rel = relationship("Producto", back_populates="transacciones")

    def __repr__(self):
        return (f"<Transaccion id={self.id} cedula={self.cedula} "
                f"total=${self.precio_final} ts={self.timestamp}>")


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
        print(f"[DB] Base de datos lista: {os.path.abspath(db_path)}")

    # ── Utilidades internas ──────────────────────────────────────────────────

    def _session(self) -> Session:
        return Session(self._engine)

    @staticmethod
    def _load_db_path_from_config() -> str:
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("base_de_datos", {}).get("ruta", _DEFAULT_DB)
        except (FileNotFoundError, KeyError):
            return _DEFAULT_DB

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
                 uso_unico: bool = False) -> Optional[dict]:
        with self._session() as s:
            qr = QRCode(codigo=codigo, producto_id=producto_id,
                        uso_unico=uso_unico)
            s.add(qr)
            s.commit()
            s.refresh(qr)
            return self._qr_to_dict(qr)

    def validar_y_usar_qr(self, codigo: str) -> Optional[dict]:
        """
        Valida si el código QR existe y está disponible.
        Si es de uso único, lo marca como usado.
        Devuelve el QR con su producto, o None si no es válido.
        """
        with self._session() as s:
            qr = s.query(QRCode).filter_by(codigo=codigo).first()
            if not qr:
                return None
            if qr.uso_unico and qr.usado:
                return None    # QR de uso único ya consumido
            if qr.uso_unico:
                qr.usado    = True
                qr.usado_at = datetime.utcnow()
                s.commit()
            return self._qr_to_dict(qr)

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
        }


# ─────────────────────────────────────────────────────────────────────────────
# Instancia global compartida (importar en los demás módulos)
# ─────────────────────────────────────────────────────────────────────────────
db = Database()
