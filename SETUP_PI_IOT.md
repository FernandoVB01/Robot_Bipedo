# Montaje IoT — Foscam + pantalla + base de datos + hotspot

Guía de puesta en marcha con la arquitectura real del proyecto: **la PC hace la
visión por computadora**, la Raspberry captura y presenta. Lo nuevo es de dónde
sale la imagen (Foscam por cable) y por dónde hablan Pi y PC (hotspot).

---

## 1. Topología

```
   [Foscam FI8918W] ──cable RJ45──> [Pi eth0]     red privada 192.168.50.0/24
    (motorizada, MJPEG)                           la Pi le da IP a la cámara
                                        │
                                   [Pi wlan0] ──┐
                                        │       │
                                   UART GPIO 14/15 ──> [ESP32] ──> VESC ──> motores
                                        │       │
                                   SPI ──> [Tontec 3.5"]
                                                │
                            ┌───────────────────┴──────────────┐
                            │      HOTSPOT DEL CELULAR         │
                            │   (WiFi local — NO gasta plan)   │
                            └───────────────────┬──────────────┘
                                                │
                                             [ PC ]  ← MediaPipe + QR + SQLite + API
                                                │
                                       datos móviles ──> Firebase
```

**Quién hace qué:**

| | Rol |
|---|---|
| **Foscam** | Entrega imagen a la Pi por cable. Se mueve por orden de la Pi |
| **Pi** | Captura, comprime a JPEG, manda a la PC. Pantalla, botones, UART al ESP32 |
| **PC** | Visión (MediaPipe + QR), base de datos SQLite, API y dashboard |
| **Celular** | Hotspot: es la "red" donde se ven Pi y PC. Sus datos, solo para Firebase |

**Por qué la cámara va por cable directo a la Pi y no al hotspot:**

- El hotspot da IPs distintas cada vez que se enciende; la cámara cambiaría de
  dirección y habría que reeditar `config.json` a diario.
- Muchos hotspots aíslan a los clientes entre sí y la Pi no la vería.
- El vídeo de la cámara se queda en el cable: no compite con el enlace Pi↔PC.

---

## 2. Qué red usar: cualquiera sirve

Al sistema le da igual **quién** haga de red, mientras Pi y PC estén en la misma.
El hotspot no tiene nada de especial: es un router más.

| Opción | Cuándo usarla | Subred típica |
|---|---|---|
| **WiFi normal (casa)** ⭐ | Si la tenés, la mejor: hay internet para instalar y para Firebase | `192.168.1.x` / `192.168.0.x` |
| **Hotspot de iPhone** | Si no hay WiFi, o la WiFi es institucional | `172.20.10.x` |
| **Hotspot de Android** | Igual que el anterior | `192.168.43.x` |

Lo único que cambia entre las tres es **la IP de la PC**, que va en `ROBOT_PC_IP`.
Ningún otro archivo se toca. La Foscam sigue por cable en su red privada
`192.168.50.x` pase lo que pase — por eso se eligió ese rango, no choca con
ninguna de las tres.

**Cuidado con la WiFi de ESPOL o cualquier red institucional:** casi siempre
tienen *aislamiento de clientes* activado (bloquean a propósito que dos equipos
se vean entre sí) y portal cautivo. Ahí el proyecto no levanta y hay que tirar
de hotspot. Se descarta en 10 segundos con un `ping` (ver Bloque C).

### Particularidades del hotspot de iPhone

- Necesita **datos celulares activos** para poder encenderse. iOS no sabe
  compartir una WiFi a la que está conectado, solo la red celular.
- Reparte direcciones `172.20.10.x` y admite pocos equipos. Tu `ROBOT_PC_IP`
  será algo como `172.20.10.2`, no `192.168.x.x`.
- **Se apaga solo** si no hay ningún equipo conectado en ~90 s. Dejá abierta la
  pantalla *Ajustes → Compartir Internet* mientras conectás el primer equipo.
- Activá **"Maximizar compatibilidad"**: fuerza 2.4 GHz y evita la mayoría de
  los problemas de conexión con la Pi.

---

## 2b. Los datos móviles: cuándo encenderlos

Esto es lo que probablemente te preocupaba, y la respuesta es buena:

> **El hotspot y los datos móviles son dos cosas distintas.** El tráfico entre
> la Pi y la PC va por la WiFi del hotspot y se conmuta ahí mismo: **nunca sale
> a la red celular, así que no consume tu plan.** Los frames de vídeo, por
> muchos que sean, son gratis.

O sea:

| Qué querés hacer | Hotspot | Datos móviles |
|---|---|---|
| Pi ↔ PC (visión, API, dashboard) | ✅ Encendido | ❌ Apagados |
| Instalar librerías en la Pi | — | ⚠️ Una WiFi normal (~80 MB, una sola vez) |
| Firebase | ✅ Encendido | ✅ Encendidos (unos KB por venta) |

En la mayoría de Android podés levantar el hotspot con los datos apagados. Si tu
teléfono te obliga a encenderlos para activarlo, no pasa nada: el enlace Pi↔PC
igual no gasta plan.

**Para hoy: hotspot ENCENDIDO, datos APAGADOS.** Firebase queda en `false` y todo
se guarda en SQLite; lo subís mañana con `sync_firebase.py` sin perder nada.

---

## Bloque A — En la Raspberry (sin internet)

### A.1 Dar IP a la Foscam

Con el cable puesto y la cámara energizada:

```bash
sudo nmcli connection add type ethernet ifname eth0 con-name foscam ipv4.method shared ipv4.addresses 192.168.50.1/24
```

```bash
sudo nmcli connection up foscam
```

`ipv4.method shared` levanta un DHCP en la Pi, así que la cámara recibe IP sola.
Esperá ~60 s: al arrancar, la FI8918W hace una pasada mecánica de reconocimiento
(gira sola de lado a lado). **Si se mueve al encenderla, está viva.**

Esto no toca el WiFi: la Pi puede seguir conectada al hotspot al mismo tiempo.

### A.2 Encontrar la IP que le tocó

```bash
cat /var/lib/NetworkManager/dnsmasq-eth0.leases
```

Si sale vacío:

```bash
ip neigh show dev eth0
```

### A.3 Comprobar que da imagen

No hace falta ningún programa de Foscam ni el "IP Camera Tool": es una URL normal.

```bash
curl -o /tmp/prueba.jpg "http://192.168.50.10/snapshot.cgi?user=admin&pwd="
```

```bash
ls -lh /tmp/prueba.jpg
```

Si pesa 20–60 KB, **la cámara ya está entregando imagen**, y ese mismo URL es el
que usa el robot.

- Usuario de fábrica de la FI8918W: `admin`, **contraseña vacía**.
- Si da 401, alguien le puso clave → reset: botón RESET de la base, 15 s.
- Si el puerto no es el 80, probá `http://192.168.50.10:88/...`.

Para entrar a su página de configuración (opcional), desde la Pi con escritorio:
`http://192.168.50.10`. Desde la PC, como la cámara está en la red privada de la
Pi, hace falta un túnel:

```bash
ssh -L 8088:192.168.50.10:80 pi@<ip-de-la-pi>
```

y abrís `http://localhost:8088`. Ahí conviene **apagar los LEDs infrarrojos**: a
corta distancia queman el papel del QR y no se decodifica.

### A.4 Poner la IP de la cámara en `config.json`

Reemplazá `192.168.50.10` por la IP real en tres sitios: `vision.camara_url`,
`vision.camara_snapshot_url` y `ptz.base_url`.

### A.5 Probar el movimiento de la cámara

```bash
python3 pi/foscam_ptz.py
```

Debe girar derecha, izquierda, arriba, abajo y volver al centro.

### A.6 Liberar el UART del ESP32

> **⚠️ ¿Raspberry Pi 5? Este apartado NO aplica — mirá `SETUP_PI5.md`.**
> En la Pi 5 el Bluetooth va por otro UART, así que `disable-bt` apaga el
> Bluetooth **para nada y te deja igual sin puerto serie**. Ahí alcanza con
> `dtoverlay=uart0-pi5`. Ojo también con `/dev/serial0`: en la Pi 5 es el
> conector de depuración, no los pines 8/10.

En la Pi 4 el Bluetooth ocupa `/dev/ttyAMA0`. Agregá a `/boot/firmware/config.txt`:

```
enable_uart=1
dtoverlay=disable-bt
```

Y en `sudo raspi-config` → *Interface Options* → *Serial Port* → consola de
login **NO**, puerto hardware **SÍ**. Después:

```bash
sudo systemctl disable --now hciuart && sudo usermod -aG dialout $USER && sudo reboot
```

Cableado (masa común obligatoria): Pi GPIO14/TX (pin 8) → ESP32 RX · Pi GPIO15/RX
(pin 10) → ESP32 TX · Pi GND (pin 6) → ESP32 GND.

### A.7 Botones — los pines cambiaron

Los de antes (17, 27, 22, 23) chocan con el panel SPI de 3.5", sobre todo GPIO 17
que muchos overlays usan para el táctil.

| Botón | GPIO | Pin físico |
|---|---|---|
| [+] | 5 | 29 |
| [−] | 6 | 31 |
| [OK] | 13 | 33 |
| [DEL] | 26 | 37 |

Una pata al GPIO, la otra a GND. Sin resistencias (pull-up interno).

---

## Bloque A.8 — Librerías en la Pi

La Pi **no lleva MediaPipe**: la IA corre en la PC. Aquí solo hace falta capturar,
comprimir, dibujar y hablar con el ESP32 — unos 80 MB en total.

```bash
source ~/mi_proyecto_env/bin/activate && pip install opencv-python-headless numpy pygame pyserial gpiozero pyzmq
```

En la PC no hay que instalar nada nuevo: ya tenía MediaPipe, FastAPI y el resto
de cuando el proyecto corría contra tu computadora.

---

## Bloque B — En la PC (dos cosas que sí o sí bloquean)

### B.1 Marcar el hotspot como red **Privada**

Cuando la PC se conecta al hotspot, Windows pregunta si es pública o privada. Si
queda como **pública**, el firewall bloquea todo lo que entre y **la Pi nunca
podrá conectarse**, aunque el `ping` funcione.

*Configuración → Red e Internet → Wi-Fi → (red del hotspot) → Perfil de red →
**Privada***.

### B.2 Abrir los puertos 5555 y 8000

En PowerShell **como administrador**, una sola vez:

```powershell
New-NetFirewallRule -DisplayName "Robot Bipedo" -Direction Inbound -Protocol TCP -LocalPort 5555,8000 -Action Allow -Profile Private
```

Sin esto, el síntoma típico es `[PI-CLIENT] WARN: timeout de red, reconectando…`
en bucle mientras en la PC no aparece ningún frame.

---

## Bloque C — Levantar el sistema

**Orden: primero la PC, después la Pi.** Si arranca antes la Pi, se queda
reintentando hasta que la PC aparezca (no se rompe, solo pierde tiempo).

### 1. La red

Conectá **la Pi y la PC a la misma WiFi**: la de tu casa si la tenés, o el
hotspot del celular si no. Da igual cuál, mientras sea la misma para las dos.

Si la Pi es headless, se conecta por consola:

```bash
sudo nmcli device wifi connect "NOMBRE_DE_LA_RED" password "LA_CLAVE"
```

```bash
nmcli device status
```

`wlan0` debe salir como `connected`. Ojo: esto **no afecta** al cable de la
Foscam, `eth0` sigue con su red privada en paralelo.

### 2. PC — averiguar su IP en esa red

```powershell
ipconfig
```

Anotá la **Dirección IPv4** del adaptador Wi-Fi: `192.168.1.x` en una WiFi de
casa, `172.20.10.x` con iPhone, `192.168.43.x` con Android.

### 2b. Comprobar que se ven — hacelo SIEMPRE antes de arrancar nada

Desde la Pi, con la IP que anotaste:

```bash
ping -c 3 192.168.1.57
```

Si no responde, no sigas: o la red tiene aislamiento de clientes (típico en WiFi
institucional → usá el hotspot), o el firewall de Windows está bloqueando
(Bloque B). Levantar los programas antes de que este `ping` funcione es perder
la tarde.

### 3. PC — arrancar el servidor

```powershell
py robot_server.py
```

Desde la carpeta `pc\`. Levanta la visión y la API en una sola ventana, e imprime
su propia IP para que la copies. Esperá a ver:

- `[DB] Base de datos lista: ...`
- `[PC-SERVER] Escuchando en tcp://*:5555`

> Si preferís las dos ventanas de siempre, `py pc_server.py` y `py api_server.py`
> por separado siguen funcionando igual.

### 4. Pi — conectarse al hotspot y lanzar la GUI

Sin editar ningún archivo, pasando la IP de la PC por variable de entorno:

```bash
ROBOT_PC_IP=192.168.43.57 python3 ~/robot_bipedo/pi/pi_gui_gpio.py
```

(activá antes el entorno: `source ~/mi_proyecto_env/bin/activate`)

Señales de que todo enganchó:

- `[PI-CLIENT] Cámara IP lista (snapshot)` → la Foscam responde
- `[PI-CLIENT] Conectado a tcp://192.168.43.57:5555` → la PC responde
- `[PTZ] Control de cámara activo` → la cámara se moverá
- `[UART] Conectado` → el ESP32 responde
- En la PC: `[PC-SERVER] frames=60 fps=...` → los frames están llegando

### 5. Dashboard

Desde cualquier dispositivo del hotspot: `http://192.168.43.57:8000`
La API completa, en `/docs`.

---

## Si algo falla

| Síntoma | Causa casi segura |
|---|---|
| `timeout de red, reconectando…` en bucle | Firewall de Windows (B.1 y B.2) |
| `No se pudo abrir la cámara IP` | IP de la Foscam mal, o cable/DHCP (A.1–A.3) |
| Frames llegan pero con segundos de retraso | Cámara demasiado lejos del rango WiFi de la Pi — no aplica por cable |
| El QR nunca se lee | Infrarrojos encendidos, o subí `camara_ancho/alto` a 800×600 |
| La cámara se mueve al revés al seguir la mano | `ptz.invertir_pan: true` |
| Ping funciona pero ZeroMQ no | El hotspot tiene aislamiento de clientes: probá el hotspot en 2.4 GHz, o desactivá esa opción en el celular |

Prueba de humo sin cámara ni ESP32, en la PC:

```powershell
py mock_client.py
```

---

## Bloque L — Llevarlo al laboratorio

En el laboratorio vas a depender del hotspot del celular. Eso **no cambia nada
del sistema**, pero exige preparación previa: allá no vas a tener ni monitor ni
la WiFi de tu casa para rescatar la Pi.

### L.1 Guardá el hotspot en la Pi AHORA (lo más importante)

Una Raspberry headless solo se conecta a redes que ya tiene guardadas. Con el
hotspot del celular encendido **hoy, en tu casa**, hacé que la Pi lo aprenda:

```bash
sudo nmcli device wifi connect "NOMBRE_DEL_HOTSPOT" password "LA_CLAVE"
```

Comprobá que quedó guardado junto a tu WiFi de casa:

```bash
nmcli connection show
```

Deben aparecer las dos, más `foscam`. NetworkManager se conecta sola a la que
esté al alcance: en casa agarra tu WiFi, en el laboratorio el hotspot. No hay
que tocar nada allá.

> Si no hacés esto hoy, en el laboratorio la Pi arranca sin red y la única
> salida es conectarle un monitor y teclado.

### L.2 Dejá fija la IP de la PC en el hotspot

Para no depender de un `ipconfig` a último momento, conectá **la laptop primero**
al hotspot (antes que la Pi): los hotspots reparten las direcciones por orden de
llegada, así que la laptop se queda con la primera —`172.20.10.2` en iPhone,
`192.168.43.2` en Android— y la Pi con la siguiente.

Poné esa dirección en `config.json` → `red.pc_ip`. Así **la configuración por
defecto del repo es la del laboratorio**, y en casa usás la variable de entorno:

```bash
ROBOT_PC_IP=192.168.1.57 python3 pi/pi_gui_gpio.py
```

En el laboratorio, sin variable ni ediciones:

```bash
python3 pi/pi_gui_gpio.py
```

### L.3 Cómo entrar a la Pi sin monitor

Desde la laptop, por nombre en vez de por IP:

```bash
ssh pi@raspberrypi.local
```

Si no resuelve, mirá la lista de dispositivos conectados en la pantalla del
hotspot del celular: ahí sale la IP de la Pi.

### L.4 Checklist del día

1. Hotspot del celular encendido (iPhone: dejá abierta la pantalla de *Compartir
   Internet* y activá *Maximizar compatibilidad*).
2. **Laptop** al hotspot — primero, para que agarre la IP esperada.
3. Foscam energizada y con su cable a la Pi.
4. Pi encendida: se conecta sola al hotspot.
5. `ping` desde la Pi a la laptop. **Si esto falla, nada más va a funcionar.**
6. Laptop: `py robot_server.py` desde `pc\`.
7. Pi: `python3 pi/pi_gui_gpio.py`.

Lo que **no** hace falta en el laboratorio: internet, instalar nada, ni la WiFi
del sitio. Todo el software ya está en los equipos y las ventas se guardan en
SQLite; Firebase se sincroniza después.

---

## Bloque D — Firebase (mañana)

1. Datos móviles **encendidos** en el celular.
2. Copiá el JSON de credenciales a `pc/firebase_credentials.json`
   (pasos en `pc/FIREBASE_SETUP.md`).
3. `firebase.activo: true` en `config.json`.
4. Reiniciá el servidor de la PC. Debe salir `[FIREBASE] Conectado`.

Las ventas hechas sin internet no se pierden: quedan pendientes en SQLite y se
suben después con:

```powershell
py sync_firebase.py
```

---

## La pantalla Tontec — dejala para el final

> **⚠️ ¿Raspberry Pi 5? Este panel no va a funcionar.** El driver `fbtft` ya no
> está en los kernels modernos y `fbcp-ili9341` no corre en la Pi 5 (usa
> registros del BCM2835 y DispmanX, que no existen ahí). Pasate a HDMI o a la
> Touch Display 2 por DSI — son dos números en `config.json`. Detalles en
> `SETUP_PI5.md`, sección 3.


El código ya es agnóstico del panel: la GUI se dibuja en un lienzo virtual de
1280×720 y se escala a lo que diga `pantalla.ancho/alto`. Pasar de HDMI al Tontec
son **dos números**, no tocar código.

**Hoy, para validar todo**, usá un monitor HDMI y poné:

```json
"pantalla": { "ancho": 1280, "alto": 720, "fullscreen": false }
```

**Después**, para el Tontec 3.5" (480×320): activá SPI en `raspi-config` y agregá
el overlay del panel a `/boot/firmware/config.txt` — `dtoverlay=mz61581` para los
Tontec MZ61581, `dtoverlay=piscreen` para los de chip ILI9486. Reiniciá y
comprobá que aparezca `/dev/fb1` con `ls /dev/fb*`.

Aviso honesto: este es el único paso del montaje que pelea. Estos paneles dibujan
en `/dev/fb1` y SDL2 (el motor de pygame) habla con `/dev/fb0`; el puente
habitual es `fbcp`, y en Bookworm con driver KMS hay que compilar la variante
`fbcp-ili9341`. Es media hora **y necesita internet para compilar**: no lo
mezcles con la puesta en marcha de hoy.

---

## Qué cambió en el proyecto

| Archivo | Cambio |
|---|---|
| `config.json` | Secciones nuevas `pantalla` y `ptz`; URLs de la Foscam; pines de botones movidos; notas de hotspot |
| `pi/foscam_ptz.py` | **Nuevo.** Mueve los motores de la cámara |
| `pc/robot_server.py` | **Nuevo.** Visión + API + BD en un solo proceso (opcional) |
| `pi/pi_zmq_client.py` | Modo snapshot para la FI8918W; hilo lector que evita el retardo acumulado; reescalado antes de enviar; reconexión automática; `ROBOT_PC_IP` |
| `pi/pi_gui_gpio.py` | Lienzo virtual escalable a cualquier panel; seguimiento con la cámara; no muere sin tarjeta de audio |
| `pi/pi_uart.py` | Ahora sí lee la sección `uart` de `config.json` |
| `pc/pc_server.py` | Una sola pasada de MediaPipe por frame (el doble de fps); devuelve la posición de la mano; ya no duplica las ventas |
| `pc/database.py` | La ruta del `.db` se resuelve contra la raíz del repo |
