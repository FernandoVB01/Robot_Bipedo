"""
=============================================================================
ROBOT BÍPEDO — Reintento de subida a Firebase
=============================================================================
Sube a Firestore todas las transacciones que quedaron pendientes en SQLite
(por ejemplo, porque no había internet en el momento de la venta).

Es seguro correrlo cuantas veces quieras: usa el id local como id del documento,
así que reintentar NO crea duplicados.

Uso:
    cd pc
    python sync_firebase.py

Ideal para dejarlo en una tarea programada (cada X minutos) o correrlo a mano
cuando vuelva la conexión.
=============================================================================
"""

import sys

try:
    from database import db
    from firebase_client import fb
except ImportError as exc:
    print(f"[SYNC] ERROR: no se pudo importar un módulo requerido: {exc}")
    sys.exit(1)


def main():
    if not fb.esta_disponible():
        print(f"[SYNC] Firebase no está disponible: {fb.motivo_no_disponible()}")
        print("[SYNC] Revisá config.json (firebase.activo) y las credenciales.")
        sys.exit(1)

    pendientes = db.get_pendientes_firebase()
    if not pendientes:
        print("[SYNC] No hay transacciones pendientes. Todo sincronizado ✔")
        return

    print(f"[SYNC] {len(pendientes)} transacciones pendientes. Subiendo...")

    ok, fallidas = 0, 0
    for t in pendientes:
        if fb.guardar_transaccion(t):
            db.marcar_sincronizado(t["id"])
            ok += 1
        else:
            fallidas += 1

    print(f"[SYNC] Listo. Subidas: {ok}  |  Fallidas: {fallidas}")
    if fallidas:
        print("[SYNC] Las fallidas siguen pendientes; volvé a correr el script.")


if __name__ == "__main__":
    main()
