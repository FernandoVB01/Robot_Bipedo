"""
=============================================================================
ROBOT BÍPEDO — Cliente Firebase (Firestore en la nube)
=============================================================================
Rol       : Espejo en la nube de las transacciones. Cada factura que se
            registra en SQLite local se envía también a Firestore para poder
            consultarla desde cualquier lado (consola de Firebase, otra PC, etc.)

Diseño    : RESILIENTE. Si falta la librería, faltan las credenciales o no hay
            internet, el módulo NO rompe el sistema: simplemente reporta que
            Firebase no está disponible y el robot sigue funcionando con SQLite.
            Las transacciones que no se puedan subir quedan marcadas como
            pendientes en SQLite y se reintentan luego con sync_firebase.py.

Configuración (config.json → "firebase"):
    activo                    : true/false para prender o apagar la nube
    archivo_credenciales      : nombre del JSON de la cuenta de servicio (en pc/)
    coleccion_transacciones   : nombre de la colección en Firestore

Dependencias:
    pip install firebase-admin

Cómo obtener las credenciales: ver pc/FIREBASE_SETUP.md
=============================================================================
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_PC_DIR = Path(__file__).parent


def _load_firebase_config() -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("firebase", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class FirebaseClient:
    """
    Envoltorio delgado sobre Firestore. Toda la inicialización es tolerante a
    fallos: cualquier problema deja al cliente en estado "no disponible" en vez
    de lanzar una excepción hacia el servidor.
    """

    def __init__(self):
        self._db = None
        self._disponible = False
        self._coleccion = "transacciones"
        self._coleccion_clientes = "clientes"
        self._motivo_no_disponible = "no inicializado"
        self._inicializar()

    # ── Inicialización ───────────────────────────────────────────────────────

    def _inicializar(self):
        cfg = _load_firebase_config()

        if not cfg.get("activo", False):
            self._motivo_no_disponible = "desactivado en config.json (firebase.activo=false)"
            print(f"[FIREBASE] Desactivado — solo se usará SQLite local.")
            return

        self._coleccion = cfg.get("coleccion_transacciones", "transacciones")
        self._coleccion_clientes = cfg.get("coleccion_clientes", "clientes")

        # 1) Verificar que la librería esté instalada
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
        except ImportError:
            self._motivo_no_disponible = (
                "librería 'firebase-admin' no instalada "
                "(pip install firebase-admin)"
            )
            print(f"[FIREBASE] WARN: {self._motivo_no_disponible}")
            return

        # 2) Verificar que exista el archivo de credenciales
        cred_nombre = cfg.get("archivo_credenciales", "firebase_credentials.json")
        cred_path = _PC_DIR / cred_nombre
        if not cred_path.exists():
            self._motivo_no_disponible = (
                f"no se encontró el archivo de credenciales '{cred_path}'. "
                f"Descárgalo desde la consola de Firebase (ver FIREBASE_SETUP.md)."
            )
            print(f"[FIREBASE] WARN: {self._motivo_no_disponible}")
            return

        # 3) Inicializar la app (idempotente aunque se importe dos veces)
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(str(cred_path))
                firebase_admin.initialize_app(cred)
            self._db = firestore.client()
            self._disponible = True
            self._motivo_no_disponible = ""
            print(f"[FIREBASE] Conectado — colección '{self._coleccion}'.")
        except Exception as exc:
            self._motivo_no_disponible = f"error al inicializar Firestore: {exc}"
            print(f"[FIREBASE] WARN: {self._motivo_no_disponible}")

    # ── API pública ──────────────────────────────────────────────────────────

    def esta_disponible(self) -> bool:
        """True si Firestore está listo para recibir datos."""
        return self._disponible

    def guardar_transaccion(self, transaccion: dict) -> bool:
        """
        Sube una transacción a Firestore. Devuelve True si tuvo éxito.
        Nunca lanza excepción: ante cualquier fallo (p. ej. sin internet)
        devuelve False para que el llamador la deje pendiente de reintento.

        Se usa el id local de SQLite como id del documento, así la escritura es
        idempotente: reintentar no crea duplicados.
        """
        if not self._disponible or self._db is None:
            return False

        try:
            doc = dict(transaccion)  # copia defensiva
            doc["subido_en"] = datetime.now(timezone.utc).isoformat()

            doc_id = str(transaccion.get("id", "")) or None
            col = self._db.collection(self._coleccion)
            if doc_id:
                col.document(doc_id).set(doc)   # set() = crea o sobreescribe
            else:
                col.add(doc)
            return True
        except Exception as exc:
            print(f"[FIREBASE] ERROR al subir transacción "
                  f"id={transaccion.get('id')}: {exc}")
            return False

    def guardar_cliente(self, cliente: dict) -> bool:
        """
        (Opcional) Espeja el registro de un cliente en Firestore, usando la
        cédula como id de documento. Útil si luego se quiere consultar el
        historial por cliente desde la nube.
        """
        if not self._disponible or self._db is None:
            return False
        try:
            cedula = str(cliente.get("cedula", "")).strip()
            if not cedula:
                return False
            self._db.collection(self._coleccion_clientes).document(cedula).set(
                cliente, merge=True
            )
            return True
        except Exception as exc:
            print(f"[FIREBASE] ERROR al subir cliente: {exc}")
            return False

    def motivo_no_disponible(self) -> str:
        """Explica por qué Firebase no está activo (para logs/diagnóstico)."""
        return self._motivo_no_disponible


# ── Instancia global compartida (importar en los demás módulos) ──────────────
fb = FirebaseClient()
