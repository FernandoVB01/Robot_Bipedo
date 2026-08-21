#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — Cliente de sesión (Raspberry Pi → PC)
=============================================================================
Rol : Le pregunta a la PC, veinte veces por segundo, qué está haciendo el
      cliente con su celular. Es el lado del robot del modelo "el celular
      escribe en la base de datos y el robot consulta hasta ver cambios".

      Pi  → POST /api/sesion/nueva            (al detectar a alguien)
      Pi  → GET  /api/sesion/<token>/estado   (20 Hz, mientras dure)

Qué devuelve el sondeo:
      · fase      → esperando / conectado / validada / comprando / finalizada
      · control   → la consigna del joystick (v, w)
      · acciones  → eventos sueltos (BAILE, GIRO_IZQ…), entregados UNA sola vez

─────────────────────────────────────────────────────────────────────────────
DOS DECISIONES QUE IMPORTAN
─────────────────────────────────────────────────────────────────────────────
1. CONEXIÓN PERSISTENTE (http.client, no urllib).
   Con urllib.request cada consulta abre y cierra un socket TCP. A 20 consultas
   por segundo eso es un saludo de tres vías por consulta y cientos de puertos
   en TIME_WAIT. Con `http.client.HTTPConnection` y keep-alive se reusa la misma
   conexión: el sondeo baja de ~15 ms a ~2 ms y deja de ensuciar la red.

2. LA FRESCURA SE MIDE ACÁ, NO ALLÁ.
   La PC dice cuán viejo es el comando en SU reloj, pero entre la PC y la Pi hay
   WiFi. Lo que decide si el robot se mueve es cuándo llegó el dato a la Pi. Si
   el enlace se cae con el acelerador apretado, `control()` devuelve None y el
   robot frena — sin esperar a que salte el hombre muerto del ESP32.

Dependencias: solo biblioteca estándar.
=============================================================================
"""

import http.client
import json
import threading
import time
from collections import deque
from typing import Optional

# Cada cuánto se le pregunta a la PC. 20 Hz = 50 ms: con eso, entre que el
# cliente suelta el joystick y el robot frena pasan ~50 ms de sondeo + ~20 ms
# de UART. Imperceptible.
INTERVALO_SONDEO_S = 0.05

# Si la consigna que tenemos es más vieja que esto, no se usa.
CONTROL_VIGENCIA_S = 0.5

# Timeout de cada consulta. Corto a propósito: es preferible perder una consulta
# y reintentar en 50 ms que quedarse colgado medio segundo con el robot andando.
TIMEOUT_HTTP_S = 0.8


class SesionClient:
    """
    Mantiene una sesión con la PC y la sondea en un hilo aparte.

    Uso típico desde la GUI:

        cli = SesionClient("192.168.43.50", 8000)
        if cli.verificar_pc():
            token, url = cli.abrir_sesion()      # el QR se dibuja con `url`
            cli.start()
            ...
            v, w = cli.control() or (0.0, 0.0)
            for a in cli.tomar_acciones(): ...
            ...
            cli.cerrar()
    """

    def __init__(self, pc_ip: str, api_puerto: int = 8000):
        self._host = f"{pc_ip}:{api_puerto}"
        self._conn: Optional[http.client.HTTPConnection] = None
        self._conn_lock = threading.Lock()

        self._token: Optional[str] = None
        self._url:   Optional[str] = None

        self._estado_lock = threading.Lock()
        self._fase   = "sin_sesion"
        self._seq    = -1
        self._cedula: Optional[str] = None
        self._producto_id: Optional[int] = None
        self._mensaje = ""
        self._v = 0.0
        self._w = 0.0
        self._control_recibido_ts = 0.0
        self._acciones: deque = deque()

        self._fallos = 0
        self._ultimo_ok_ts = 0.0

        self._hilo: Optional[threading.Thread] = None
        self._parar = threading.Event()

    # ── Conexión ─────────────────────────────────────────────────────────────

    def _pedir(self, metodo: str, ruta: str, cuerpo: Optional[dict] = None):
        """Petición HTTP reusando la conexión. Devuelve dict, o None si falló."""
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        cabeceras = {"Content-Type": "application/json"} if datos else {}

        with self._conn_lock:
            for intento in (1, 2):      # el 2º es tras reabrir la conexión
                try:
                    if self._conn is None:
                        self._conn = http.client.HTTPConnection(
                            self._host, timeout=TIMEOUT_HTTP_S)
                    self._conn.request(metodo, ruta, body=datos, headers=cabeceras)
                    resp = self._conn.getresponse()
                    crudo = resp.read()
                    if resp.status >= 400:
                        return {"__error__": resp.status}
                    return json.loads(crudo) if crudo else {}
                except Exception:
                    # La conexión persistente se cae sola cada tanto (el servidor
                    # la recicla). Se reabre y se reintenta una vez.
                    try:
                        if self._conn:
                            self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                    if intento == 2:
                        return None
        return None

    def verificar_pc(self) -> bool:
        """
        ¿Está viva la PC? Se consulta ANTES de dibujar el QR.

        Sin esto, el fallo más probable en una demo es un QR perfectamente
        escaneable que lleva a una IP que ya no existe (la del hotspot cambia
        cada vez que se reinicia). Mejor mostrar un error en pantalla que dejar
        al cliente esperando a que cargue una página que nunca va a cargar.
        """
        r = self._pedir("GET", "/api/stats")
        return r is not None and "__error__" not in r

    # ── Ciclo de vida de la sesión ───────────────────────────────────────────

    def abrir_sesion(self) -> Optional[tuple[str, str]]:
        """Abre una sesión en la PC. Devuelve (token, url) o None."""
        r = self._pedir("POST", "/api/sesion/nueva", {})
        if not r or "token" not in r:
            print("[SESION] No se pudo abrir la sesión en la PC.")
            return None
        with self._estado_lock:
            self._token = r["token"]
            self._url   = r["url"]
            self._fase  = r.get("fase", "esperando")
            self._seq   = -1
            self._cedula = None
            self._producto_id = None
            self._v = self._w = 0.0
            self._control_recibido_ts = 0.0
            self._acciones.clear()
        print(f"[SESION] Abierta: {self._url}")
        return self._token, self._url

    def cerrar(self, cancelar: bool = True):
        """Detiene el sondeo y, si se pide, cancela la sesión en la PC."""
        self._parar.set()
        if self._hilo and self._hilo.is_alive():
            self._hilo.join(timeout=1.0)
        if cancelar and self._token:
            self._pedir("POST", f"/api/sesion/{self._token}/cancelar", {})
        with self._estado_lock:
            self._token = None
            self._fase  = "sin_sesion"
            self._v = self._w = 0.0
            self._control_recibido_ts = 0.0

    def start(self):
        """Arranca el hilo de sondeo."""
        if self._hilo and self._hilo.is_alive():
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._loop, daemon=True,
                                      name="SesionSondeo")
        self._hilo.start()

    # ── Lectura del estado ───────────────────────────────────────────────────

    def fase(self) -> str:
        with self._estado_lock:
            return self._fase

    def cedula(self) -> Optional[str]:
        with self._estado_lock:
            return self._cedula

    def producto_id(self) -> Optional[int]:
        with self._estado_lock:
            return self._producto_id

    def url(self) -> Optional[str]:
        with self._estado_lock:
            return self._url

    def control(self) -> Optional[tuple[float, float]]:
        """
        Consigna vigente del joystick, o None si está vieja o no hay.
        Devolver None es la señal de "no te muevas": la GUI la traduce en freno.
        """
        with self._estado_lock:
            if not self._control_recibido_ts:
                return None
            if (time.time() - self._control_recibido_ts) > CONTROL_VIGENCIA_S:
                return None
            return self._v, self._w

    def tomar_acciones(self) -> list:
        """Devuelve las acciones pendientes y las quita. Se consumen una vez."""
        with self._estado_lock:
            pendientes = list(self._acciones)
            self._acciones.clear()
        return pendientes

    def enlace_ok(self) -> bool:
        """False si hace rato que la PC no contesta."""
        return (time.time() - self._ultimo_ok_ts) < 2.0 if self._ultimo_ok_ts else False

    # ── Hilo de sondeo ───────────────────────────────────────────────────────

    def _loop(self):
        print("[SESION] Sondeo iniciado.")
        while not self._parar.is_set():
            inicio = time.time()

            with self._estado_lock:
                token, seq = self._token, self._seq

            if not token:
                time.sleep(0.2)
                continue

            r = self._pedir("GET", f"/api/sesion/{token}/estado?since={seq}")

            if r is None or "__error__" in r:
                self._fallos += 1
                if self._fallos == 5:
                    print("[SESION] La PC no responde — el robot no se moverá.")
                if r is not None and r.get("__error__") == 404:
                    # La sesión venció o la reemplazó otro cliente.
                    with self._estado_lock:
                        self._fase = "expirada"
                        self._v = self._w = 0.0
                        self._control_recibido_ts = 0.0
            else:
                self._fallos = 0
                self._ultimo_ok_ts = time.time()
                self._aplicar(r)

            # Ritmo estable: se descuenta lo que tardó la consulta.
            resto = INTERVALO_SONDEO_S - (time.time() - inicio)
            if resto > 0:
                self._parar.wait(resto)

        print("[SESION] Sondeo detenido.")

    def _aplicar(self, r: dict):
        with self._estado_lock:
            self._seq = r.get("seq", self._seq)
            fase_nueva = r.get("fase", self._fase)
            if fase_nueva != self._fase:
                print(f"[SESION] fase: {self._fase} → {fase_nueva}")
            self._fase        = fase_nueva
            self._cedula      = r.get("cedula")
            self._producto_id = r.get("producto_id")
            self._mensaje     = r.get("mensaje", "")

            ctrl = r.get("control") or {}
            if ctrl.get("vigente"):
                self._v = float(ctrl.get("v", 0.0))
                self._w = float(ctrl.get("w", 0.0))
                # El reloj que vale es el de la Pi: entre la PC y nosotros hay WiFi.
                self._control_recibido_ts = time.time()
            else:
                self._v = self._w = 0.0
                self._control_recibido_ts = 0.0

            for a in r.get("acciones", []):
                self._acciones.append(a)


# ─────────────────────────────────────────────────────────────────────────────
# PRUEBA RÁPIDA:  python3 pi_sesion_client.py [ip_de_la_pc] [puerto]
#
# Levantá la PC (py robot_server.py) y corré esto. Después abrí la URL que
# imprime, en el celular o en el navegador, y movés el joystick: acá tiene que
# verse la consigna cambiando en vivo.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    ip     = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    puerto = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

    cli = SesionClient(ip, puerto)

    print(f"Probando la PC en {ip}:{puerto}…")
    if not cli.verificar_pc():
        print("La PC no responde. Revisá la IP, el firewall y que esté corriendo.")
        raise SystemExit(1)
    print("PC viva.")

    datos = cli.abrir_sesion()
    if not datos:
        raise SystemExit(1)
    token, url = datos
    print(f"\nAbrí esto en el celular:  {url}\n")
    cli.start()

    try:
        anterior = None
        while True:
            fase = cli.fase()
            ctrl = cli.control()
            acciones = cli.tomar_acciones()
            ahora = (fase, ctrl, tuple(a["accion"] for a in acciones))
            if ahora != anterior or acciones:
                print(f"  fase={fase:<12} control={ctrl}  "
                      f"acciones={[a['accion'] for a in acciones]}")
                anterior = ahora
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nCerrando…")
        cli.cerrar()
