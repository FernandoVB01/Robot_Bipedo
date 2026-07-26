# Visor de telemetría en tiempo real

Grafica en pantalla lo que emite la ESP32 y lo guarda en CSV. Corre igual en la
Raspberry (pantalla del robot) y en la PC.

## Instalación

```bash
pip install pygame pyserial numpy
```

En la Raspberry, con el entorno del proyecto activo:

```bash
source ~/mi_proyecto_env/bin/activate
pip install pyserial
```

## Uso hoy mismo (Etapa A, sin VESC)

Con la ESP32 que ya tienes flasheada emitiendo `tiempo_ms,ax,ay,az,gx,gy,gz`:

```bash
python telemetry_viewer.py --port COM7
```

El visor detecta solo el formato, calcula el pitch con filtro complementario y
lo grafica. **Cierra antes el monitor serie de PlatformIO**: un puerto COM
admite un solo usuario.

Qué verificar en esta etapa (es el objetivo de la Etapa A):

| Comprobación | Qué debes ver |
|---|---|
| Robot quieto | `\|a\|` cerca de 9.81 m/s² (el visor lo pinta verde si está bien) |
| Inclinar hacia adelante | El pitch debe subir **positivo**. Si baja, usa `--pitch-mode xz_inv` |
| Otro montaje de la IMU | Prueba `--pitch-mode yz` o `yz_inv` hasta que el signo sea correcto |
| Giroscopio quieto | Cerca de cero; una deriva constante es el bias a compensar |
| Frecuencia | Debe rondar los 100 Hz del firmware |

Anota el `--pitch-mode` que funcionó: ese valor va después al firmware de
balanceo (`PITCH_SIGN` / eje en `config.h`).

## Uso en la Raspberry (etapas D en adelante)

```bash
python telemetry_viewer.py --port /dev/ttyAMA0 --fullscreen
```

## Teclas

| Tecla | Acción |
|---|---|
| `SHIFT`+`A` | Armar (`START`). Requiere SHIFT a propósito, para no armar sin querer |
| `X` | **PARAR** (`STOP`) — tecla de pánico |
| `T` | Telemetría on/off (`TELE_ON` / `TELE_OFF`) |
| `E` | Fijar el cero de pitch en la postura actual (`EQ`) |
| `C` | Captura en ráfaga a frecuencia completa (`CAPTURA`) |
| `L` | Iniciar/parar el log CSV en `logs/` |
| `P` | Pausar la gráfica (los datos se siguen recibiendo y logueando) |
| `R` | Limpiar buffers y reiniciar el filtro |
| `Q` / `ESC` | Salir |

**Seguridad:** al salir, al cerrar la ventana o ante cualquier excepción, el
visor envía `STOP`. Nunca arma solo.

## Formatos que entiende

**1. IMU cruda** (firmware actual del MPU6050):

```
tiempo_ms,ax,ay,az            (m/s²)
tiempo_ms,ax,ay,az,gx,gy,gz   (m/s² y rad/s)
```

**2. Telemetría de balanceo** (firmware `esp32_balance`):

```
T,tiempo_ms,pitch_deg,gyro_rads,u_nm,i_a,erpm,estado
```

Las líneas que empiezan con `#`, `ACK:` o `ERR:` se muestran en la consola
inferior en vez de graficarse. Cualquier otra cosa se descarta y se cuenta en
el contador "descartadas" (si crece, hay ruido en el enlace).

## Parche necesario en `pi_uart.py` del repo del equipo

`UARTController` manda un comando y **espera el `ACK:` leyendo la siguiente
línea**. Cuando la telemetría esté activa, entre el comando y su ACK van a
llegar decenas de líneas `T,...` y el lector daría el comando por fallido.

El arreglo es descartar todo lo que no sea respuesta, en el punto donde se lee
el ACK:

```python
# Antes:
respuesta = self._serial.readline().decode(errors="ignore").strip()

# Después: ignora telemetría y comentarios mientras espera la respuesta.
LIMITE = time.time() + TIMEOUT_S
respuesta = ""
while time.time() < LIMITE:
    linea = self._serial.readline().decode(errors="ignore").strip()
    if not linea:
        continue
    if linea.startswith("ACK:") or linea.startswith("ERR:"):
        respuesta = linea
        break
    # 'T,...' y '#...' son telemetría: no son la respuesta al comando.
```

Alternativa si prefieres no tocar ese archivo: deja la telemetría apagada
(`TELE_OFF`) durante la operación comercial y enciéndela solo para diagnóstico.
Por eso el firmware arranca con la telemetría **apagada**.
