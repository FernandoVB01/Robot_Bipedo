# Arquitectura IoT — el celular del cliente como control del robot

Documento de referencia de la capa interactiva: cómo se conectan ESP32,
Raspberry Pi, PC y el teléfono del cliente, y qué se dicen exactamente.

Complementa a `DOCUMENTACION_PROYECTO.md` (visión general) y a
`ROADMAP_BALANCEO.md` (el robot autoequilibrado, que va por su carril).

---

## 1. El flujo, en cinco pasos

1. **Detección y acercamiento.** La cámara ve a una persona. La Pi cierra un
   lazo de control y manda al ESP32 hacia ella hasta frenar enfrente.
2. **Invitación.** El robot pausa su ruta, Pingüi saluda y la pantalla **genera
   un código QR**.
3. **Conexión.** El cliente escanea el QR con su celular, se abre una WebApp e
   ingresa su cédula.
4. **Control simultáneo.** Con la cédula validada, el cliente maneja el robot
   desde su teléfono *y a la vez* navega el catálogo en la pantalla del robot.
5. **Cierre.** Al terminar la compra el sistema **libera el control**, el ESP32
   recibe la orden de girar y el robot retoma su ruta.

---

## 2. Arquitectura

```
                          ┌──────────────────────────────────────┐
   Foscam FI8918W         │            RASPBERRY PI 4            │
   (motorizada) ──RJ45───►│  · captura y comprime a JPEG         │
   red privada            │  · pantalla: Pingüi + QR de sesión   │
   192.168.50.x           │  · máquina de estados                │
                          │  · lazo de acercamiento              │
                          └───┬──────────────┬───────────────┬───┘
                              │              │               │
                  ZeroMQ REQ/REP :5555   HTTP :8000      UART 115200
                  (frames → banderas)   (sondeo 20 Hz)   (ASCII)
                              │              │               │
                              ▼              ▼               ▼
                        ┌─────────────────────────┐   ┌──────────────┐
                        │           PC            │   │    ESP32     │
                        │ · visión (persona/mano/ │   │  2 núcleos   │
                        │   QR) — MediaPipe       │   │  · enlace Pi │
                        │ · SQLite + sesiones     │   │  · movimiento│
                        │ · WebApp del celular    │   │  · odometría │
                        └───┬─────────────────┬───┘   └──────┬───────┘
                            │                 │              │ UART binario
              WiFi hotspot  │                 │ internet     ▼
                            ▼                 ▼         ┌──────────────┐
                     ┌─────────────┐     ┌──────────┐   │ VESC maestra │
                     │  CELULAR    │     │ Firebase │   │    ID 42     │
                     │  (WebApp)   │     │ (ventas) │   └──────┬───────┘
                     └─────────────┘     └──────────┘          │ CAN
                                                               ▼
                                                        ┌──────────────┐
                                                        │ VESC esclava │
                                                        │    ID 25     │
                                                        └──────────────┘
```

### Por qué cada cosa está donde está

| Capa | Hace | Por qué ahí |
|---|---|---|
| **ESP32** | Duty de cada rueda, giros, odometría, hombre muerto | FreeRTOS da tiempos deterministas. Linux no garantiza que el lazo corra cada 20 ms, y de eso depende que el robot frene cuando tiene que frenar |
| **Raspberry Pi** | Pantalla, cámara, máquina de estados, UART | Es el único que está físicamente en el robot y tiene la pantalla y el puerto serie |
| **PC** | Visión por IA, base de datos, WebApp | Los modelos no le dan a la Pi con latencia usable. Es la decisión que ya estaba tomada y sigue siendo la correcta |
| **Celular** | Cédula, joystick, catálogo | Es el teclado y el mando que el cliente ya trae en el bolsillo |
| **Firebase** | Copia de las ventas | Persistencia consultable desde fuera. **Fuera del lazo de control** |

---

## 3. Dónde vive "la base de datos" y por qué importa

El modelo es el que pidió el equipo: **el celular escribe en una base de datos y
el robot la consulta continuamente hasta ver cambios.** Lo único que se eligió
con cuidado es *dónde* está esa base.

Está en la **PC**, en el mismo hotspot que el celular y que la Pi.

| | Por la PC (elegido) | Por Firebase |
|---|---|---|
| Ida y vuelta de un comando | ~30-50 ms | 200-500 ms |
| Al soltar el acelerador | frena | sigue medio segundo |
| Necesita internet | no | sí |

Medio segundo no se nota al ingresar una cédula, pero sí cuando alguien intenta
frenar el robot antes de que choque. Por eso el **manejo** va contra la PC y la
**venta** se sigue espejando a Firebase igual que siempre.

### Presupuesto de latencia (dedo del cliente → ruedas)

| Tramo | Tiempo |
|---|---|
| Celular → PC (WiFi hotspot, HTTP) | ~5-15 ms |
| Espera del sondeo de la Pi (20 Hz) | 0-50 ms |
| PC → Pi (HTTP con conexión persistente) | ~2-5 ms |
| Pi → ESP32 (UART 115200, ~15 bytes) | ~2 ms |
| Tick del lazo de movimiento | 0-20 ms |
| **Total** | **~30-90 ms** |

Para comparar: el tiempo de reacción de una persona es de unos 250 ms.

---

## 4. Protocolo UART — Raspberry Pi ↔ ESP32

ASCII orientado a líneas, 115200 baud. Se mantuvo el formato de texto en vez de
pasar a binario a propósito: se puede depurar con un monitor serie y escribir
comandos a mano, que es exactamente lo que hace falta mientras se calibra.

**Cableado (masa común obligatoria):**
Pi GPIO14/TX (pin 8) → ESP32 GPIO16 · Pi GPIO15/RX (pin 10) → ESP32 GPIO17 ·
GND (pin 6) → GND.

### 4.1 Pi → ESP32

| Comando | Parámetros | Qué hace | ACK |
|---|---|---|---|
| `MOV:<v>:<w>` | v,w ∈ [-1,1] | Teleoperación diferencial. v = avance, w = giro | **no** |
| `GIRO:<grados>` | ± | Giro sobre el propio eje, lazo cerrado. + = horario | sí |
| `PIVOTE:<I\|D>:<grados>:<k>` | k ∈ [0,1] | Arco: pivotea sobre una rueda **y avanza** | sí |
| `RUTINA:<nombre>` | BAILE, SALUDO, GIRO_180 | Secuencia enlatada | sí |
| `MODO:<modo>` | AUTO, TELEOP, PAUSA | Modo de operación | sí |
| `PARAR` | — | Alto inmediato **con freno activo** | sí |
| `CAL:<param>:<valor>` | ver §7 | Calibración en caliente, persistida en NVS | sí |
| `PING` | — | Vida del enlace | `PONG` |
| `RODAR:<duty>` | [-1,1] | Rodado continuo (modo atracción) | sí |
| `AVANZAR_T:<ms>:<duty>` | | Avance temporizado | sí |
| `AVANZAR` · `ACERCAR` · `RETROCEDER` · `GIRAR_180` | — | Compatibilidad | sí |

Respuestas: `ACK:<comando>` · `ERR:DESCONOCIDO` · `ERR:PARAMETROS:<comando>` ·
`ERR:CAL_PARAM:<param>` · `ERR:COLA_LLENA`.

**`MOV:` no lleva ACK, y es a propósito.** Se manda diez veces por segundo;
esperar la confirmación de cada uno metería el ida-y-vuelta del serie dentro del
lazo del joystick. La red de seguridad no es el ACK, es el hombre muerto (§4.3).

### 4.2 ESP32 → Pi (sin que nadie los pida)

```
TEL:<modo>:<duty_izq>:<duty_der>:<tach>:<vbat>:<flags>     cada 200 ms
EVT:<nombre>                                                cuando pasa algo
```

- `modo`: 0=AUTO, 1=TELEOP, 2=PAUSA
- `tach`: cuentas del tacómetro de la VESC maestra
- `flags`: `O` = odometría viva · `X` = obstáculo · `-` = no
- Eventos: `LISTO` (arrancó), `DEADMAN` (saltó el hombre muerto),
  `RUTINA_FIN` (terminó una secuencia)

La `O` de los flags es el diagnóstico más útil del sistema: si ahí hay un guion,
la VESC no está contestando y **todos los giros están yendo a ciegas por
cronómetro**.

### 4.3 El hombre muerto

En modo `TELEOP`, si pasan **400 ms sin un `MOV:` nuevo**, el ESP32 frena y
emite `EVT:DEADMAN`.

Es el seguro más importante de todo el sistema. Sin él, un corte de WiFi, un
celular que se bloquea o un cliente que cierra la pestaña con el acelerador
apretado dejan al robot andando solo.

Hay tres capas, y cada una tapa un agujero distinto:

| Capa | Detecta |
|---|---|
| La WebApp manda ceros al soltar el dedo | El caso normal |
| La Pi descarta consignas de más de 500 ms | Se cayó el enlace PC↔Pi |
| El ESP32 frena a los 400 ms sin `MOV` | Se cayó el enlace Pi↔ESP32, o se colgó la Pi |

> **Probarlo antes de dejar que alguien maneje:** mandá `MODO:TELEOP`, después
> `MOV:0.3:0`, y dejá de mandar. A los ~400 ms las ruedas tienen que frenar y
> tiene que aparecer `EVT:DEADMAN`. Si siguen girando, no conectes el celular.

### 4.4 Regla: en TELEOP nunca se manda `PARAR`

`PARAR` saca al firmware del estado de teleoperación. Los `MOV:` siguientes
actualizarían la consigna pero nadie la leería, y el joystick quedaría muerto
hasta reiniciar. Para frenar durante la teleoperación se manda **`MOV:0:0`**,
que frena igual y mantiene vivo el lazo.

---

## 5. Protocolo de sesión — Celular ↔ PC ↔ Pi

La sesión vive en la PC (`pc/sesion.py`) y la sirve `pc/api_server.py`.

| Endpoint | Quién | Para qué |
|---|---|---|
| `POST /api/sesion/nueva` | Pi | Abre sesión → `{token, url}` |
| `GET /c/{token}` | Celular | La WebApp |
| `GET /api/sesion/{token}` | Celular | Su propio estado |
| `POST /api/sesion/{token}/cedula` | Celular | Valida Módulo 10 → gana el control |
| `POST /api/sesion/{token}/control` | Celular | `{v, w}` — joystick, ~10 Hz |
| `POST /api/sesion/{token}/accion` | Celular | `{accion}` — BAILE, GIRO_IZQ… |
| `POST /api/sesion/{token}/producto` | Celular | Elige del catálogo |
| `POST /api/sesion/{token}/finalizar` | Celular | Registra la venta y libera el control |
| `POST /api/sesion/{token}/cancelar` | Celular / Pi | Cierra sin comprar |
| `GET /api/sesion/{token}/estado?since=N` | **Pi, 20 Hz** | La consulta continua |

### Tres decisiones que evitan accidentes

**1. El control es último-valor-gana, no una cola.** Si el WiFi hipa y llegan
cinco posiciones del joystick juntas, el robot obedece la última. Ejecutarlas en
fila significaría obedecer órdenes de hace un segundo: un acelerador viejo es
peor que ningún acelerador.

**2. Las acciones sí son una cola, y se consumen una sola vez.** "Que baile" es
un evento discreto: si el cliente aprieta dos veces, tienen que pasar las dos
cosas. El campo `seq` sube con cada cambio y la Pi manda el último que vio en
`since`, así el servidor sabe qué entregar.

**3. El `seq` NO sube con el joystick.** Si subiera, la Pi vería "algo cambió"
diez veces por segundo y la detección de cambios no serviría para nada. El
bloque `control` viaja siempre; `seq` es solo para los cambios de fase.

### Fases

```
esperando ──escanea──► conectado ──cédula OK──► validada ──elige──► comprando
    │                       │                       │                   │
    └───────────────────────┴───────────────────────┴─────► expirada    │
                                                                        ▼
                                                                   finalizada
```

El **token de un solo uso** también hace de seguridad: sin él, cualquier teléfono
del hotspot podría manejar el robot.

---

## 6. Máquina de estados de la Raspberry

```
IDLE ──persona (3 cuadros)──► ACERCAMIENTO ──llegó──► INVITACION
 ▲                                  │                      │ escanean
 │                            (se fue / 12 s)              ▼
 │                                  │              ESPERA_CEDULA
 │                                  │                      │ cédula válida
 │                                  │                      ▼
 │                                  │                   TELEOP ◄── el cliente
 │                                  │                      │        maneja
 │                                  │                      │ compra
 │                                  │                      ▼
 │                                  │                   FACTURA
 │                                  ▼                      ▼
 └──────── GIRO:180 ◄────────── DESPEDIDA ◄────────────────┘
                                (baile)
```

**Ningún estado se queda colgado.** Todos tienen tope de tiempo
(`config.json` → `sesion`). Un robot congelado esperando a un cliente que ya se
fue es peor que uno que se equivoca: deja de trabajar.

---

## 7. El giro de 90° que daba 106-110°

Un error del ~18%, siempre para el mismo lado y siempre parecido, no es ruido:
es **inercia**. El firmware anterior "paraba" poniendo duty = 0, que en una VESC
significa **rueda libre** — el motor deja de empujar pero el robot sigue de
largo con lo que traía.

Se ataca en tres capas, y cada una sirve aunque las otras no estén:

**1. Freno activo.** Al terminar el giro se manda `COMM_SET_CURRENT_BRAKE` en
vez de duty = 0. Esto solo se lleva la mayor parte del error.

**2. Calibración persistida.** `ms_grado` se ajusta por UART y queda en la NVS
del ESP32:

```
CAL:ms_grado:6.0
CAL:guardar:1
```

Fórmula: `ms_grado_nuevo = ms_grado_actual × (90 / grados_medidos)`.
Con los 108° medidos: `7.2 × (90/108) = 6.0`.

**3. Lazo cerrado por odometría.** El firmware ahora **lee** la VESC
(`COMM_GET_VALUES`) y saca el tacómetro. Girando sobre el eje:

```
θ = 2 · (2π·R · vueltas) / vía        vueltas = cuentas / cpr
```

Con esto el giro deja de depender del tiempo, y por lo tanto de la carga de la
batería, del piso y del peso del robot — que es lo que hace que una calibración
por cronómetro se desajuste sola de un día para el otro.

**Si la VESC no contesta**, el firmware lo detecta (flag `O` en `TEL:`) y cae
solo al giro por tiempo. Nunca se queda esperando.

### Parámetros calibrables (comando `CAL:`)

| Parámetro | Qué es | Cómo medirlo |
|---|---|---|
| `ms_grado` | ms de giro por grado | `GIRO:90`, medir con transportador |
| `giro_duty` | duty de los giros | probar; más bajo = más repetible |
| `cpr` | cuentas de tacómetro por vuelta de rueda | anotar `tach`, girar la rueda una vuelta a mano, restar |
| `radio_mm` | radio de la rueda | cinta métrica (hub de 168 mm → 84) |
| `via_mm` | separación entre ruedas | cinta métrica, centro a centro |
| `freno_a` | corriente de frenado | subir si se pasa, bajar si se sacude |
| `anticipo` | dónde empieza a frenar | ajuste fino, después de `freno_a` |

`CAL:listar:0` imprime todo. `CAL:guardar:1` lo deja en NVS.

---

## 8. El repertorio de movimiento

La tracción es **diferencial de verdad**: cada rueda tiene su propio duty. El
firmware anterior mandaba el mismo valor a las dos, así que solo podía ir
derecho o de reversa.

| Movimiento | Ruedas | Comando |
|---|---|---|
| Adelante / atrás | izq = der | `MOV:±v:0` |
| **Giro sobre el propio eje** | izq = −der | `MOV:0:±w` · `GIRO:±90` |
| **Pivote (gira y avanza)** | una más lenta que la otra | `PIVOTE:D:90:0.3` |
| Curva mientras se maneja | mezcla | `MOV:v:w` |

Mezcla de teleoperación: `izq = v + w`, `der = v − w`, normalizando si se pasa
de 1. Se normaliza en vez de recortar para que al acelerar y girar a la vez no
se pierda el giro — recortar cambiaría la proporción entre las dos ruedas.

`RUTINA:BAILE` es el baile estilo Club Penguin: +90, −90, −90, +90, un pasito
adelante y atrás, y una vuelta completa. Se ejecuta con la misma máquina no
bloqueante, así que **se puede cortar en cualquier momento**.

---

## 9. Reparto de los dos núcleos del ESP32

```
Core 0 (PRO_CPU)                    Core 1 (APP_CPU)
┌────────────────────────┐          ┌────────────────────────┐
│ task_motion   (prio 6) │          │ task_pi_link  (prio 5) │
│  lazo de 20 ms         │◄─cola────│  lee el UART de la Pi  │
│  → duty a las ruedas   │          │  parsea y contesta ACK │
├────────────────────────┤          └────────────────────────┘
│ task_vesc_rx  (prio 5) │
│  arma paquetes VESC    │
│  → tacómetro, batería  │
└────────────────────────┘
```

Lo que habla con las llantas va junto en el core 0, para que el tiempo entre
"leo el tacómetro" y "decido si freno" sea corto y parejo. El enlace con la Pi
va aislado en el core 1: una ráfaga de comandos no puede robarle tiempo al lazo
que controla los motores.

**Nada bloquea.** La versión anterior ejecutaba los avances temporizados y los
giros dentro de un `while` que dejaba sorda a la tarea de control: un `PARAR`
mandado a mitad de un avance de 5 segundos se quedaba esperando en la cola. Con
alguien manejando desde un celular, eso es inaceptable. Ahora todo es una
máquina de estados que avanza en ticks y **cualquier comando nuevo interrumpe al
anterior en el acto**.

---

## 10. Cosas que van a fallar en la demo (y cómo evitarlas)

### El celular se pasa a datos móviles
Android e iOS detectan que el hotspot "no tiene internet" y se van a la red
celular. Ahí la WebApp deja de cargar aunque todo esté bien.

- **Android:** al conectarse, elegir *Mantener conexión* cuando pregunte.
  Ajustes → Red → Wi-Fi → *Cambiar a datos móviles automáticamente*: **apagado**.
- **iPhone:** Ajustes → Wi-Fi → (la red) → *Asistencia Wi-Fi*: **apagada**.

### La IP de la PC cambia con el hotspot
El QR se arma con el **Host que usó la Pi para pedir la sesión**, no con una IP
de configuración. O sea que apunta siempre a una dirección que ya se demostró
alcanzable. Además, antes de dibujar el QR la Pi comprueba que la PC responda:
si no, muestra un error en pantalla en vez de un código que no lleva a ninguna
parte.

### La WebApp no puede depender de internet
No tiene un solo recurso externo: ni CDN, ni fuentes, ni librerías. Todo está
dentro de `pc/static/control.html`.

### El QR no se lee
Se dibuja **negro sobre blanco** con 4 módulos de margen. Si aun así cuesta,
subir `sesion.qr_lado_px` antes de tocar cualquier otra cosa.

---

## 11. Qué instalar

**En la Raspberry** (una sola vez, con internet):
```bash
pip install pygame numpy pyserial pyzmq opencv-python-headless gpiozero qrcode
```
`qrcode` es nuevo. Va **sin** `[pil]`: solo se usa para calcular la matriz de
módulos y se dibuja con Pygame, así que no hace falta Pillow. Si falta, el robot
no se cae — muestra la URL en texto.

**En la PC:**
```bash
pip install fastapi uvicorn sqlalchemy pyzmq opencv-python mediapipe
```
El modelo de detección de personas (~14 MB) lo descarga solo `pc_server.py` la
primera vez que arranca con internet.

---

## 12. Orden de encendido

1. Hotspot encendido; **la PC se conecta primero** (así toma la IP esperada).
2. PC: `py robot_server.py` desde `pc\`.
3. Comprobar desde la Pi: `ping <ip-de-la-pc>`. Si falla, no seguir.
4. Pi: `python3 pi/pi_gui_gpio.py`.
5. Celular al hotspot, con la asistencia de datos apagada.

En la pantalla del robot, abajo, dice si la PC responde y si el ESP32 está
conectado. Si algo está en rojo, se ve antes de que llegue el primer cliente.
