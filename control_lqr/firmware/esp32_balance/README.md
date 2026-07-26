# Firmware de balanceo (ESP32, ESP-IDF)

El ESP32 es el cerebro del balanceo: lee la MPU6050, estima el ángulo, calcula
el torque y lo manda como **corriente** a las dos VESC. La Raspberry queda
fuera del lazo: solo manda órdenes y recibe telemetría.

Estado: **compila y enlaza** (RAM 6%, Flash 23%). Falta la puesta en marcha
sobre hardware, que se hace por etapas (abajo).

## Compilar y flashear

```bash
pio run                 # compilar
pio run -t upload       # flashear
pio device monitor      # ver la consola
```

## Conexiones

| ESP32 | Va a | Nota |
|---|---|---|
| GPIO21 / GPIO22 | MPU6050 SDA / SCL | I2C a 400 kHz |
| GPIO4 / GPIO5 | VESC maestra RX / TX | UART2, protocolo binario |
| GPIO16 / GPIO17 | Pi TX / Pi RX | UART1, ASCII |
| GND | **común entre Pi, ESP32 y VESC** | la causa #1 de fallos del equipo |

## Comandos (por el UART de la Pi **o** por USB)

| Comando | Qué hace |
|---|---|
| `PING` | Prueba de vida |
| `START` | Arma. Falla si el robot no está a menos de 10° del equilibrio |
| `STOP` / `PARAR` | Desarma y corta corriente |
| `TELE_ON` / `TELE_OFF` | Telemetría a 50 Hz (arranca apagada) |
| `EQ` | Fija el cero de pitch en la postura actual |
| `GET` | Muestra ganancias, equilibrio y estado |
| `K kth kdth kx kdx` | Cambia ganancias **en caliente**, sin reflashear |
| `I <amperios>` | Corriente fija de prueba (solo desarmado) — Etapa 1 |
| `CAPTURA` | Graba 3 s a 200 Hz y los vuelca después |
| `VDUMP` | Vuelca en hex la última respuesta de la VESC |
| `AVANZAR` `ACERCAR` `RETROCEDER` `GIRAR_180` | Compatibles con el protocolo que ya usa la Pi |

## Antes de confiar en nada: cuatro números que hay que verificar

Están todos en `src/config.h` y **ninguno se puede dar por bueno sin medirlo**:

1. **`PITCH_SIGN`** — que al inclinar hacia adelante el pitch salga positivo.
   Se comprueba en la Etapa A con `telemetry_viewer.py`.
2. **`KT_NM_PER_A`** — convierte torque a corriente. El datasheet del motor es
   inconsistente (la fila `Torque_Max` no cuadra con las otras dos). Medirlo:
   rueda bloqueada con un brazo de palanca conocido sobre una báscula,
   aplicar corriente con `I` y calcular `Kt = F·brazo / I`.
3. **`MOTOR_POLE_PAIRS`** — se lee en VESC Tool. Sin esto, la velocidad
   estimada está escalada mal y `kx`/`kdx` desestabilizan.
4. **`RPM_OFFSET`** en `vesc.c` — dónde cae el campo `rpm` dentro de
   `GET_VALUES`. Depende de la versión del firmware VESC. Comprobar con
   `VDUMP` comparando contra el valor que muestra VESC Tool.

## Puesta en marcha por etapas

**Regla de oro: ruedas en el aire hasta que la etapa esté aprobada en el aire.**

| Etapa | Qué hacer | Se aprueba cuando |
|---|---|---|
| 1 | `I 1.0` con las ruedas en el aire | Giran **las dos** ruedas (maestra por UART, esclava por CAN). `I 0` para parar |
| 2 | `TELE_ON` y mirar el visor | El ángulo es suave, sin deriva, y el signo es correcto |
| 2b | `VDUMP` y comparar con VESC Tool | El ERPM que reporta coincide |
| 3 | `K 2 0 0 0`, luego `START`, **ruedas en el aire** | Al inclinar hacia adelante las ruedas giran **hacia adelante** (contrarrestan la caída). Si giran al revés → invertir `PITCH_SIGN` |
| 4 | Ruedas en el piso, con tether y una persona sosteniendo. Subir `kth` de a poco, luego `kdth` | Se sostiene firme, sin temblar |
| 5 | Activar `kx` y `kdx` (requiere ERPM correcto) | Deja de derivar: se queda en su sitio |
| 6 | `AVANZAR` / `GIRAR_180` desde la Pi | Navega manteniendo el equilibrio |

En la Etapa 4 se usa `K` para sintonizar sin reflashear, y `CAPTURA` para ver
la respuesta transitoria a frecuencia completa. Ese volcado se compara en
MATLAB contra la simulación: es la validación experimental del modelo.

## Seguridad implementada

- Arranca **desarmado** y con telemetría apagada. Solo `START` habilita torque.
- `START` se rechaza si el robot no está cerca del equilibrio (armar tumbado
  provocaría un latigazo de torque).
- Corte por inclinación a 30°: pasa a `FAULT`, corta corriente y **se engancha**
  ahí hasta un `STOP` + `START` explícitos.
- Si la IMU deja de responder, desarma y corta.
- Saturaciones en cascada: torque total, corriente por rueda, error de posición
  y de velocidad, y rampas en las referencias.
- El torque de giro está acotado aparte para que nunca le robe autoridad al
  balance.
- El visor manda `STOP` al cerrarse.

## Relación con el modelo de MATLAB

La ley de control es realimentación de estado:

```
u = kth·δθ + kdth·δθ̇ + kx·(x − x_ref) + kdx·(ẋ − v_ref)
```

Con `kx = kdx = 0` **esto es exactamente el PID de ángulo** del
`ROADMAP_BALANCEO.md` del equipo. Activando `kx`/`kdx` queda el LQR de 4
estados: mismo código, mismas pruebas, activación progresiva.

Las ganancias son la `K` de MATLAB cambiada de signo (`lqr()` las devuelve
negativas porque allí la ley es `u = −K·x`). Referencia a 200 Hz **con los
parámetros de ejemplo, no los del robot real**:
`kth = 9.43, kdth = 1.12, kx = 0.96, kdx = 1.73`.
