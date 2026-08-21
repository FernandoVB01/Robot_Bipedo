#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — Controlador UART Serie (Raspberry Pi → ESP32)
=============================================================================
Entorno virtual : /home/pi/mi_proyecto_env
Activar antes de ejecutar:
    source ~/mi_proyecto_env/bin/activate

Protocolo (ASCII, orientado a líneas):
  Raspberry Pi envía: "<COMANDO>\n"
  ESP32 responde:     "ACK:<COMANDO>\n"  o  "ERR:<motivo>\n"
  ESP32 además emite, sin que nadie se lo pida:
      "TEL:<modo>:<duty_izq>:<duty_der>:<tach>:<vbat>:<flags>\n"   (5 Hz)
      "EVT:<nombre>\n"   (DEADMAN, RUTINA_FIN, LISTO)

Comandos:
  ── Movimiento diferencial (lo nuevo) ──────────────────────────────────────
  MOV:<v>:<w>          Teleoperación. v = avance, w = giro, ambos [-1..1].
                       SIN ACK, a propósito (ver send_fast).
  GIRO:<grados>        Giro sobre el propio eje. + = horario.
  PIVOTE:<I|D>:<g>:<k> Arco: pivotea sobre una rueda y avanza a la vez.
  RUTINA:<nombre>      BAILE | SALUDO | GIRO_180
  MODO:<AUTO|TELEOP|PAUSA>
  CAL:<param>:<valor>  Calibración en caliente (persistida en NVS del ESP32)
  PING                 → responde PONG

  ── Compatibles con la versión anterior ────────────────────────────────────
  AVANZAR · PARAR · GIRAR_180 · ACERCAR · RETROCEDER
  RODAR:<duty> · AVANZAR_T:<ms>:<duty>

Conexión física (GPIO ↔ ESP32):
  Pi  TX  (GPIO 14, pin 8)  → ESP32 RX  (GPIO 16)
  Pi  RX  (GPIO 15, pin 10) → ESP32 TX  (GPIO 17)
  GND (pin 6)               → ESP32 GND
  ⚠️  Masa común obligatoria entre Pi, ESP32 y VESC.

Configuración en la Pi (una sola vez):
  /boot/firmware/config.txt →  enable_uart=1  ·  dtoverlay=disable-bt
  sudo raspi-config → Interface Options → Serial → login NO / puerto SÍ

Dependencias:
    pip install pyserial

─────────────────────────────────────────────────────────────────────────────
POR QUÉ HAY UN HILO LECTOR
─────────────────────────────────────────────────────────────────────────────
Antes `send_command()` escribía y hacía `readline()` ahí mismo. Ahora el ESP32
manda telemetría por su cuenta cada 200 ms, así que ese `readline()` se comería
un "TEL:..." creyendo que era el ACK. Con un único hilo lector, cada línea va a
donde corresponde: los ACK a quien esté esperando, la telemetría al estado.
=============================================================================
"""

import glob
import json
import queue
import serial
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
# Se lee de config.json (sección "uart"); los valores de aquí son el respaldo.
_CFG_PATH = Path(__file__).parent.parent / "config.json"
try:
    _CFG = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
except Exception:
    _CFG = {}
_U = _CFG.get("uart", {})

UART_PORT     = _U.get("puerto", "/dev/ttyAMA0")   # UART hardware de la Pi 4
# Alternativas: "/dev/serial0" (alias seguro) o "/dev/ttyUSB0" (adaptador USB)
BAUD_RATE     = _U.get("baud_rate", 115200)
TIMEOUT_S     = _U.get("timeout_s", 1.0)
MAX_RETRIES   = _U.get("max_reintentos", 3)
RETRY_DELAY_S = 0.2

# Comandos sin parámetros
VALID_COMMANDS = {
    "AVANZAR",
    "PARAR",
    "GIRAR_180",
    "ACERCAR",
    "RETROCEDER",
    "PING",
}

# Comandos que llevan parámetros: se validan por prefijo
VALID_PREFIXES = (
    "AVANZAR_T:",
    "RODAR:",
    "MOV:",
    "GIRO:",
    "PIVOTE:",
    "RUTINA:",
    "MODO:",
    "CAL:",
)


def _modelo_pi() -> str:
    """Modelo de la placa, de /proc/device-tree. Cadena vacía si no es una Pi."""
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().decode("utf-8", "replace").strip("\x00").strip()
    except OSError:
        return ""


def diagnosticar_puerto(puerto: str) -> None:
    """
    Avisa de los dos errores de puerto serie que más tiempo hacen perder, en vez
    de dejar que el robot falle con un ENOENT pelado.

    1. EN LA RASPBERRY PI 5, `/dev/serial0` NO ES GPIO14/15.
       En la Pi 4 `serial0` apuntaba al UART de los pines 8/10, así que medio
       internet recomienda usarlo. En la Pi 5 apunta al **conector de depuración
       de 3 pines**: el puerto abre sin error, no da ningún mensaje raro, y el
       ESP32 sencillamente nunca contesta. En la Pi 5 hay que usar
       `/dev/ttyAMA0` y habilitarlo con `dtoverlay=uart0-pi5`.

    2. El puerto no existe. Se listan los que sí hay, que casi siempre revela
       el problema de un vistazo.
    """
    import os

    modelo = _modelo_pi()
    es_pi5 = "Raspberry Pi 5" in modelo

    if es_pi5 and puerto.rstrip("0123456789").endswith("serial"):
        print("[UART] ¡OJO! En la Raspberry Pi 5, /dev/serial0 es el conector de "
              "DEPURACIÓN, no los pines 8/10.")
        print("[UART]       Usá /dev/ttyAMA0 y poné 'dtoverlay=uart0-pi5' en "
              "/boot/firmware/config.txt")

    if not os.path.exists(puerto):
        disponibles = sorted(glob.glob("/dev/ttyAMA*") + glob.glob("/dev/ttyUSB*")
                             + glob.glob("/dev/ttyS*") + glob.glob("/dev/serial*"))
        print(f"[UART] El puerto {puerto} no existe.")
        print(f"[UART] Disponibles: {', '.join(disponibles) or 'ninguno'}")
        if es_pi5:
            print("[UART] En la Pi 5, para tener el UART en los pines 8/10 hace "
                  "falta 'dtoverlay=uart0-pi5' en /boot/firmware/config.txt "
                  "(y NO hace falta disable-bt: el Bluetooth va por otro UART).")
        if modelo:
            print(f"[UART] Placa detectada: {modelo}")


class UARTController:
    """
    Gestiona la comunicación serie con el ESP32.

    Thread-safe. Un hilo lector consume todo lo que llega y reparte:
      · ACK/ERR/PONG → a la cola de respuestas (los espera send_command)
      · TEL:         → al diccionario de telemetría
      · EVT:         → al callback de eventos, si se registró uno
    """

    def __init__(self, port: str = UART_PORT, baud: int = BAUD_RATE):
        self._port      = port
        self._baud      = baud
        self._serial: Optional[serial.Serial] = None
        self._lock      = threading.Lock()     # serializa las ESCRITURAS
        self._connected = False

        self._respuestas: queue.Queue = queue.Queue()
        self._lector: Optional[threading.Thread] = None
        self._parar_lector = threading.Event()

        self._tel_lock = threading.Lock()
        self._telemetria: dict = {}
        self._on_evento: Optional[Callable[[str], None]] = None

    # ── API PÚBLICA ──────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Abre el puerto serie y lanza el hilo lector. True si tuvo éxito."""
        diagnosticar_puerto(self._port)
        try:
            self._serial = serial.Serial(
                port          = self._port,
                baudrate      = self._baud,
                bytesize      = serial.EIGHTBITS,
                parity        = serial.PARITY_NONE,
                stopbits      = serial.STOPBITS_ONE,
                timeout       = 0.2,        # corto: lo usa el hilo lector
                write_timeout = TIMEOUT_S,
            )
            # Pequeña pausa para que el ESP32 salga de reset tras abrir el puerto
            time.sleep(0.1)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            self._connected = True

            self._parar_lector.clear()
            self._lector = threading.Thread(target=self._loop_lector, daemon=True,
                                            name="UARTLector")
            self._lector.start()

            print(f"[UART] Conectado a {self._port} @ {self._baud} baud")
            return True
        except serial.SerialException as e:
            print(f"[UART] ERROR al abrir {self._port}: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Frena el robot, cierra el puerto y detiene el hilo lector."""
        if self._connected:
            try:
                self.parar()
            except Exception:
                pass
        self._parar_lector.set()
        if self._lector and self._lector.is_alive():
            self._lector.join(timeout=1.0)
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected = False
        print("[UART] Puerto serie cerrado.")

    def on_evento(self, callback: Callable[[str], None]):
        """Registra un callback para los EVT: del ESP32 (DEADMAN, RUTINA_FIN…)."""
        self._on_evento = callback

    # ── Envío ────────────────────────────────────────────────────────────────

    def send_command(self, command: str) -> bool:
        """
        Envía un comando y ESPERA el ACK. Reintenta hasta MAX_RETRIES.
        Para el joystick usá send_fast(): esperar ACK diez veces por segundo
        agrega un viaje de ida y vuelta al lazo de control.
        """
        command = command.strip().upper()
        if not self._es_valido(command):
            print(f"[UART] Comando inválido: '{command}'")
            return False
        if not self._connected or not self._serial:
            print("[UART] No conectado — ignorando comando.")
            return False

        with self._lock:
            for intento in range(1, MAX_RETRIES + 1):
                try:
                    # Se vacía la cola: cualquier respuesta anterior es basura
                    # de un comando que ya venció.
                    while not self._respuestas.empty():
                        self._respuestas.get_nowait()

                    self._serial.write(f"{command}\n".encode("utf-8"))
                    self._serial.flush()

                    try:
                        respuesta = self._respuestas.get(timeout=TIMEOUT_S)
                    except queue.Empty:
                        print(f"[UART] Intento {intento}: sin respuesta del ESP32.")
                        time.sleep(RETRY_DELAY_S)
                        continue

                    if respuesta.startswith("ACK:") or respuesta == "PONG":
                        return True
                    if respuesta.startswith("ERR:"):
                        print(f"[UART] ESP32 reporta error: {respuesta}")
                        return False

                except serial.SerialException as e:
                    print(f"[UART] Error serie en intento {intento}: {e}")
                    time.sleep(RETRY_DELAY_S)

        print(f"[UART] Falló el envío de '{command}' tras {MAX_RETRIES} intentos.")
        return False

    def send_fast(self, command: str) -> bool:
        """
        Envía sin esperar respuesta. Es el camino del joystick.

        A 10 comandos por segundo, esperar el ACK de cada uno metería el
        round-trip del serie dentro del lazo de control y el robot respondería
        con retraso. El ESP32 tampoco contesta los MOV: por el mismo motivo.
        La red de seguridad es el hombre muerto del firmware: si estos comandos
        dejan de llegar, el robot frena solo a los 400 ms.
        """
        if not self._connected or not self._serial:
            return False
        try:
            with self._lock:
                self._serial.write(f"{command.strip().upper()}\n".encode("utf-8"))
            return True
        except serial.SerialException as e:
            print(f"[UART] Error en send_fast: {e}")
            return False

    # ── Atajos de alto nivel ────────────────────────────────────────────────

    def mov(self, v: float, w: float) -> bool:
        """Consigna de teleoperación. v = avance, w = giro, ambos [-1..1]."""
        v = max(-1.0, min(1.0, float(v)))
        w = max(-1.0, min(1.0, float(w)))
        return self.send_fast(f"MOV:{v:.2f}:{w:.2f}")

    def parar(self) -> bool:
        """Alto inmediato con freno activo."""
        return self.send_command("PARAR")

    def rodar(self, duty: float) -> bool:
        """Rodado continuo (modo atracción)."""
        return self.send_command(f"RODAR:{duty:.2f}")

    def giro(self, grados: float) -> bool:
        """Giro sobre el propio eje. Positivo = horario."""
        return self.send_command(f"GIRO:{grados:.1f}")

    def pivote(self, lado: str, grados: float, k: float = 0.3) -> bool:
        """
        Arco pivotando sobre una rueda: gira y avanza a la vez.
        lado: 'I' o 'D'. k = velocidad de la rueda interna (0 = clavada).
        """
        lado = "I" if str(lado).upper().startswith("I") else "D"
        return self.send_command(f"PIVOTE:{lado}:{grados:.1f}:{k:.2f}")

    def rutina(self, nombre: str) -> bool:
        """BAILE, SALUDO o GIRO_180."""
        return self.send_command(f"RUTINA:{nombre.upper()}")

    def modo(self, modo: str) -> bool:
        """AUTO, TELEOP o PAUSA."""
        return self.send_command(f"MODO:{modo.upper()}")

    def calibrar(self, parametro: str, valor: float) -> bool:
        """Ajusta un parámetro del ESP32 en caliente. 'guardar' lo fija en NVS."""
        return self.send_command(f"CAL:{parametro}:{valor}")

    def ping(self) -> bool:
        return self.send_command("PING")

    # ── Telemetría ───────────────────────────────────────────────────────────

    def telemetria(self) -> dict:
        """
        Último TEL: recibido:
          modo, duty_izq, duty_der, tach, vbat, odo_ok, obstaculo, edad_s
        Diccionario vacío si el ESP32 todavía no habló.
        """
        with self._tel_lock:
            d = dict(self._telemetria)
        if d.get("ts"):
            d["edad_s"] = round(time.time() - d["ts"], 2)
        return d

    @property
    def is_connected(self) -> bool:
        return self._connected and self._serial is not None and self._serial.is_open

    # ── Interno ──────────────────────────────────────────────────────────────

    @staticmethod
    def _es_valido(command: str) -> bool:
        return (command in VALID_COMMANDS
                or command.startswith(VALID_PREFIXES))

    def _loop_lector(self):
        """Único punto de lectura del puerto. Reparte cada línea según su tipo."""
        buffer = b""
        while not self._parar_lector.is_set():
            try:
                datos = self._serial.read(128)
                if not datos:
                    continue
                buffer += datos
                while b"\n" in buffer:
                    linea, buffer = buffer.split(b"\n", 1)
                    texto = linea.decode("utf-8", errors="replace").strip()
                    if texto:
                        self._despachar(texto)
            except serial.SerialException as e:
                if not self._parar_lector.is_set():
                    print(f"[UART] Lector: error serie — {e}")
                    time.sleep(0.5)
            except Exception as e:      # noqa: BLE001 — el lector nunca debe morir
                print(f"[UART] Lector: {e}")
                time.sleep(0.2)

    def _despachar(self, texto: str):
        if texto.startswith("TEL:"):
            self._parsear_telemetria(texto)
        elif texto.startswith("EVT:"):
            evento = texto[4:]
            print(f"[UART] Evento del ESP32: {evento}")
            if self._on_evento:
                try:
                    self._on_evento(evento)
                except Exception as e:
                    print(f"[UART] Callback de evento falló: {e}")
        else:
            # ACK / ERR / PONG → para quien esté esperando en send_command
            self._respuestas.put(texto)

    def _parsear_telemetria(self, texto: str):
        # TEL:<modo>:<duty_izq>:<duty_der>:<tach>:<vbat>:<flags>
        partes = texto.split(":")
        if len(partes) < 7:
            return
        try:
            flags = partes[6]
            with self._tel_lock:
                self._telemetria = {
                    "modo":       int(partes[1]),
                    "duty_izq":   float(partes[2]),
                    "duty_der":   float(partes[3]),
                    "tach":       int(partes[4]),
                    "vbat":       float(partes[5]),
                    "odo_ok":     "O" in flags,
                    "obstaculo":  "X" in flags,
                    "ts":         time.time(),
                }
        except (ValueError, IndexError):
            pass


# ─────────────────────────────────────────────────────────────────────────────
# PRUEBA RÁPIDA (ejecutar en la Pi, con las RUEDAS EN EL AIRE)
#
#     python3 pi_uart.py
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ctrl = UARTController()
    ctrl.on_evento(lambda e: print(f"  ** EVENTO: {e}"))

    if not ctrl.connect():
        print("No se pudo conectar al ESP32. Verificá el cableado y el puerto.")
        raise SystemExit(1)

    print("\n=== Enlace ===")
    print(f"  PING → {'PONG' if ctrl.ping() else 'sin respuesta'}")
    time.sleep(0.5)
    print(f"  Telemetría: {ctrl.telemetria()}")

    print("\n=== Movimiento diferencial (ruedas en el aire) ===")
    pruebas = [
        ("Avanzar",           lambda: ctrl.mov(0.25, 0.0)),
        ("Girar sobre el eje", lambda: ctrl.mov(0.0, 0.30)),
        ("Parar",             ctrl.parar),
        ("Giro de 90° der.",  lambda: ctrl.giro(90)),
        ("Giro de 90° izq.",  lambda: ctrl.giro(-90)),
        ("Pivote derecho",    lambda: ctrl.pivote("D", 90, 0.3)),
    ]
    for nombre, accion in pruebas:
        print(f"  {nombre:<20} → {'OK' if accion() else 'FALLO'}")
        time.sleep(2.0)

    print("\n=== Prueba del HOMBRE MUERTO (la importante) ===")
    print("  Poniendo TELEOP y acelerando; después dejo de mandar comandos.")
    ctrl.modo("TELEOP")
    for _ in range(10):                 # 1 segundo de acelerador
        ctrl.mov(0.25, 0.0)
        time.sleep(0.1)
    print("  Dejo de mandar MOV. Las ruedas deben frenar solas en ~400 ms…")
    time.sleep(1.5)
    tel = ctrl.telemetria()
    frenado = abs(tel.get("duty_izq", 1)) < 0.01 and abs(tel.get("duty_der", 1)) < 0.01
    print(f"  Resultado: {'FRENÓ ✔' if frenado else 'SIGUE ANDANDO ✘ — NO conectes el celular'}")

    ctrl.modo("AUTO")
    ctrl.disconnect()
