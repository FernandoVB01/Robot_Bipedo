# Robot autoequilibrado — Hoja de ruta (Camino B: balanceo en el ESP32)

Plan metódico para construir el robot de dos ruedas que se mantiene erguido solo.
Cada etapa se **prueba y aprueba** antes de pasar a la siguiente. Regla de oro:
**todo se prueba con las ruedas en el aire hasta estar seguros.**

---

## Arquitectura (Camino B)

```
 MPU6050 ──I2C──► ESP32 ──UART──► VESC maestra (ID 42) ──► rueda 1
 (ángulo)      (lazo de balanceo)      │ CAN
                    ▲                   └──► VESC esclava (ID 25) ──► rueda 2
                    │ UART
              Raspberry Pi 4 (órdenes: AVANZAR / GIRAR)
```

El **ESP32 es el cerebro del balanceo**: lee el ángulo, corre el PID y le manda
**corriente (torque)** a las dos ruedas. Las VESC solo hacen control de motor (FOC).

## Decisiones fijas

- **Variable de control: corriente** — comando VESC `COMM_SET_CURRENT` (no duty).
- **Filtro de ángulo: complementario** (acelerómetro + giroscopio).
- **Frecuencia del lazo: ~200–500 Hz.**
- **Reúso:** el protocolo binario ESP32↔VESC ya construido (CRC16, paquetes, CAN
  forward al ID 25), cambiando `SET_DUTY` por `SET_CURRENT`.

## Conexiones

**ESP32 ↔ MPU6050 (I2C):**
| ESP32 | MPU6050 |
|---|---|
| GPIO21 (SDA) | SDA |
| GPIO22 (SCL) | SCL |
| 3.3V | VCC |
| GND | GND |

**ESP32 ↔ VESC maestra (UART2):**
| ESP32 | VESC |
|---|---|
| GPIO4 (TX) | RX |
| GPIO5 (RX) | TX |
| GND | GND |

**ESP32 ↔ Raspberry Pi (UART1, para navegación — Etapa 6):**
| ESP32 | Pi |
|---|---|
| GPIO16 (RX) | GPIO14 / TX (pin 8) |
| GPIO17 (TX) | GPIO15 / RX (pin 10) |
| GND | GND (pin 6) |

> La MPU6050 debe ir **rígidamente fija** al chasis superior, con sus ejes
> alineados y sin vibración suelta. Todas las masas en común.

---

## Etapas

### Etapa 0 — Preparación y seguridad
- Banco con **ruedas en el aire**.
- **Kill switch** de la VESC accesible en todo momento.
- MPU6050 lista, motores detectados por FOC. ✅

### Etapa 1 — Camino de corriente ESP32 → VESC (sin balanceo)
- **Objetivo:** mandar corriente a las dos ruedas desde el ESP32.
- **Éxito:** con un comando de corriente chico, giran las **dos** ruedas
  (maestra directa + esclava por CAN).
- **Seguridad:** corriente baja, pulsos cortos, ruedas despejadas.

### Etapa 2 — Ángulo de pitch confiable
- **Objetivo:** leer la MPU6050 y sacar el ángulo con el filtro complementario.
- **Éxito:** el ángulo es suave, sin deriva, con signo correcto y "cero"
  calibrado en la postura de equilibrio. **Sin cerrar ningún lazo.**

### Etapa 3 — Chequeo del SIGNO (la etapa que salva el robot)
- **Objetivo:** ángulo → PID (solo Kp bajo) → corriente, **ruedas en el aire**.
- **Éxito:** al inclinar hacia adelante, las ruedas giran para **contrarrestar**
  la caída (hacia adelante). Si giran al revés → signo invertido, corregir.
- **Por qué:** signo invertido = el robot acelera su propia caída. Siempre en el aire.

### Etapa 4 — Primer balanceo real (con asistencia)
- **Objetivo:** que se sostenga solo.
- Ruedas en el piso, alguien sosteniéndolo/tether. Subir Kp → Kd → (Ki), de a poco.
- **Seguridad:** corte por inclinación (si |pitch| > ~30°, apagar motores).
- **Éxito:** se para solo, firme, sin temblar violentamente.

### Etapa 5 — Navegación (avanzar / girar)
- Avanzar = correr el setpoint de inclinación. Girar = corriente diferencial.

### Etapa 6 — Integración con la Pi
- La Pi manda AVANZAR / GIRAR → el ESP32 lo traduce. Se enchufa con el sistema
  interactivo (cámara, QR, cédula, Firebase) ya existente.

### Etapa 7 — Endurecimiento
- Watchdog (si el ESP32 se cuelga, la VESC corta por timeout).
- Límites de corriente, rampas suaves, arranque sin salto.

---

## Principios de seguridad (siempre)

1. **Ruedas en el aire** hasta que una etapa esté aprobada en el aire.
2. **Chequear el signo** del lazo antes de que toque el piso (Etapa 3).
3. **Corte por inclinación** y por corriente máxima.
4. **Kill switch** a mano en cada prueba.
5. **Ganancias bajas primero**, subir de a poco.
6. Persona sosteniendo + tether/espuma en el primer balanceo.

## Parámetros a sintonizar (referencia)
- **Kp, Kd, Ki** del PID de balanceo.
- **Constante del filtro complementario** (peso giroscopio vs acelerómetro).
- **Offset de pitch** (el "cero" de equilibrio).
- **Corriente máxima** y **ángulo de corte**.
