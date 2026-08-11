#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT BÍPEDO — Controlador UART Serie (Raspberry Pi → ESP32)
=============================================================================
Entorno virtual : /home/pi/mi_proyecto_env
Activar antes de ejecutar:
    source ~/mi_proyecto_env/bin/activate

Protocolo (ASCII, orientado a líneas):
  Raspberry Pi envía: "<COMANDO>\n"
  ESP32 responde:     "ACK:<COMANDO>\n"  o  "ERR:<motivo>\n"

Comandos definidos:
  AVANZAR     → El robot avanza en línea recta
  PARAR       → Detiene todos los motores (freno)
  GIRAR_180   → Gira 180° y queda en dirección opuesta
  ACERCAR     → Avance lento de aproximación
  RETROCEDER  → Marcha atrás

Conexión física (GPIO ↔ ESP32):
  Pi  TX  (GPIO 14, pin 8)  → ESP32 RX  (GPIO 16 por defecto en esp32_main.c)
  Pi  RX  (GPIO 15, pin 10) → ESP32 TX  (GPIO 17 por defecto)
  GND (pin 6)               → ESP32 GND

  ⚠️  La Pi opera a 3.3 V, el ESP32 también tolera 3.3 V en sus pines IO.
      No conectar 5 V directamente al ESP32.

Configuración en la Pi (una sola vez):
  sudo raspi-config → Interface Options → Serial → No (login shell) / Yes (port)
  O bien editar /boot/config.txt: enable_uart=1

Dependencias:
    ✅ Ya instaladas en el entorno:
        (ninguna de este módulo)

    ⚠️  Pendientes de instalar (ejecutar con el entorno activo):
        pip install pyserial
=============================================================================
"""

import json
import serial
import threading
import time
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
# Se lee de config.json (sección "uart"); los valores de aquí son el respaldo.
_CFG_PATH = Path(__file__).parent.parent / "config.json"
try:
    _U = json.loads(_CFG_PATH.read_text(encoding="utf-8")).get("uart", {})
except Exception:
    _U = {}

UART_PORT     = _U.get("puerto", "/dev/ttyAMA0")   # UART hardware de la Pi 4
# Alternativas: "/dev/serial0" (alias seguro) o "/dev/ttyUSB0" (adaptador USB-Serial)
BAUD_RATE     = _U.get("baud_rate", 115200)
TIMEOUT_S     = _U.get("timeout_s", 1.0)           # Timeout de lectura de ACK
MAX_RETRIES   = _U.get("max_reintentos", 3)        # Reintentos si no se recibe ACK
RETRY_DELAY_S = 0.2

VALID_COMMANDS = {
    "AVANZAR",
    "PARAR",
    "GIRAR_180",
    "ACERCAR",
    "RETROCEDER",
}


class UARTController:
    """
    Gestiona la comunicación serie con el ESP32.
    Thread-safe: los comandos se envían desde un hilo dedicado mediante una cola.
    """

    def __init__(self, port: str = UART_PORT, baud: int = BAUD_RATE):
        self._port    = port
        self._baud    = baud
        self._serial: Optional[serial.Serial] = None
        self._lock    = threading.Lock()
        self._connected = False

    # ── API PÚBLICA ──────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Abre el puerto serie. Devuelve True si tuvo éxito."""
        try:
            self._serial = serial.Serial(
                port        = self._port,
                baudrate    = self._baud,
                bytesize    = serial.EIGHTBITS,
                parity      = serial.PARITY_NONE,
                stopbits    = serial.STOPBITS_ONE,
                timeout     = TIMEOUT_S,
                write_timeout = TIMEOUT_S,
            )
            # Pequeña pausa para que el ESP32 salga de reset tras abrir el puerto
            time.sleep(0.1)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            self._connected = True
            print(f"[UART] Conectado a {self._port} @ {self._baud} baud")
            return True
        except serial.SerialException as e:
            print(f"[UART] ERROR al abrir {self._port}: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Cierra el puerto serie."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected = False
        print("[UART] Puerto serie cerrado.")

    def send_command(self, command: str) -> bool:
        """
        Envía un comando al ESP32 y espera el ACK.
        Thread-safe. Devuelve True si el ACK fue recibido correctamente.

        Args:
            command: Uno de VALID_COMMANDS.
        """
        command = command.strip().upper()
        # AVANZAR_T:<ms>:<duty> y RODAR:<duty> llevan parámetros, se aceptan por prefijo.
        es_valido = (command in VALID_COMMANDS
                     or command.startswith("AVANZAR_T:")
                     or command.startswith("RODAR:"))
        if not es_valido:
            print(f"[UART] Comando inválido: '{command}'. "
                  f"Válidos: {VALID_COMMANDS}, 'AVANZAR_T:<ms>:<duty>' o 'RODAR:<duty>'")
            return False

        if not self._connected or not self._serial:
            print("[UART] No conectado — ignorando comando.")
            return False

        with self._lock:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    # Enviar comando con terminador de línea
                    payload = f"{command}\n".encode("utf-8")
                    self._serial.write(payload)
                    self._serial.flush()

                    # Esperar ACK
                    raw = self._serial.readline()
                    if not raw:
                        print(f"[UART] Intento {attempt}: sin respuesta del ESP32.")
                        time.sleep(RETRY_DELAY_S)
                        continue

                    response = raw.decode("utf-8", errors="replace").strip()
                    print(f"[UART] ESP32 responde: '{response}'")

                    if response.startswith("ACK:"):
                        return True
                    elif response.startswith("ERR:"):
                        print(f"[UART] ESP32 reporta error: {response}")
                        return False

                except serial.SerialException as e:
                    print(f"[UART] Error serie en intento {attempt}: {e}")
                    time.sleep(RETRY_DELAY_S)

        print(f"[UART] Falló el envío de '{command}' tras {MAX_RETRIES} intentos.")
        return False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._serial is not None and self._serial.is_open


# ─────────────────────────────────────────────────────────────────────────────
# PRUEBA RÁPIDA (ejecutar directamente en la Pi)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ctrl = UARTController()

    if ctrl.connect():
        print("\n=== Secuencia de prueba ===")
        for cmd in ["AVANZAR", "PARAR", "GIRAR_180", "AVANZAR", "PARAR"]:
            ok = ctrl.send_command(cmd)
            print(f"  {cmd} → {'OK' if ok else 'FALLO'}")
            time.sleep(1.5)
        ctrl.disconnect()
    else:
        print("No se pudo conectar al ESP32. Verifica el cableado y el puerto.")
