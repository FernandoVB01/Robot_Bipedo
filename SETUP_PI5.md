# Puesta en marcha en Raspberry Pi 5

Guía específica para la Pi 5. **`SETUP_PI_IOT.md` sigue valiendo para todo lo
demás** (Foscam, hotspot, firewall de Windows, orden de arranque); acá está solo
lo que la Pi 5 cambió, que es más de lo que parece.

> **Si venís de la Pi 4:** hay exactamente **tres** cosas que te van a morder —
> el UART, el GPIO y la pantalla SPI. Las tres tienen que ver con que la Pi 5
> movió todo el GPIO a un chip nuevo (el RP1) y con que el kernel moderno
> eliminó los drivers viejos de pantallas SPI.

---

## Resumen de lo que cambia

| | Raspberry Pi 4 | Raspberry Pi 5 |
|---|---|---|
| UART en pines 8/10 | `enable_uart=1` + `dtoverlay=disable-bt` | **`dtoverlay=uart0-pi5`** |
| `disable-bt` | **obligatorio** (el BT ocupaba ttyAMA0) | **innecesario** (el BT va por otro UART) |
| `/dev/serial0` | = pines 8/10 | = **conector de depuración** ⚠️ |
| `/dev/ttyAMA0` | = pines 8/10 (tras disable-bt) | = pines 8/10 (tras `uart0-pi5`) |
| GPIO | `RPi.GPIO` o `gpiozero` | `RPi.GPIO` **no funciona**; `gpiozero`+`lgpio` |
| Panel SPI (Tontec) | `fbcp-ili9341`, con trabajo | **no viable** — ver §3 |
| Alimentación | 5V 3A | **5V 5A** (o el USB queda capado a 600 mA) |
| Refrigeración | opcional | **activa, obligatoria** |

Lo bueno: **el código del robot no cambia**. Ni `config.json` (el puerto ya es
`/dev/ttyAMA0`, que es correcto en las dos), ni el firmware del ESP32, ni la PC.

---

## 1. El UART — el que más tiempo hace perder

### La trampa

En la Pi 4, `/dev/serial0` era un alias cómodo de los pines 8/10, y medio
internet lo recomienda por eso. **En la Pi 5, `/dev/serial0` apunta al conector
de depuración de 3 pines.**

Y esto no falla ruidosamente: el puerto **abre sin error**, no aparece ningún
mensaje raro, y el ESP32 sencillamente nunca contesta. Podés pasarte una tarde
revisando cables.

> `pi_uart.py` ahora detecta este caso y te avisa por consola al arrancar.

### Qué configurar

En `/boot/firmware/config.txt`, agregá al final:

```
dtoverlay=uart0-pi5
```

Eso es **todo**. No hace falta `enable_uart=1`, ni `dtoverlay=disable-bt`, ni
desactivar `hciuart`: en la Pi 5 el Bluetooth vive en un PL011 aparte del
BCM2712 y no compite por los pines 8/10. Si copiás las instrucciones de la Pi 4
vas a apagar el Bluetooth para nada y **seguir sin UART**.

```bash
sudo reboot
```

### Ojo con `raspi-config`

En la Pi 5, *Interface Options → Serial Port* actúa sobre la **consola y el
conector de depuración**, no sobre los pines 8/10. Contestar "login shell NO /
puerto hardware SÍ" no te da `ttyAMA0` en los pines 8/10. El overlay de arriba
sí.

### Verificación

```bash
ls -l /dev/serial* /dev/ttyAMA*
```

Tenés que ver `/dev/ttyAMA0`. Fijate además a dónde apunta `serial0`: si apunta
a `ttyAMA10`, ese es el conector de depuración — confirma lo de arriba.

Prueba de bucle, sin el ESP32: puenteá **GPIO14 (pin 8) con GPIO15 (pin 10)** con
un cable y:

```bash
python3 -c "import serial,time; s=serial.Serial('/dev/ttyAMA0',115200,timeout=1); s.write(b'hola\n'); time.sleep(.2); print('eco:', s.readline())"
```

Si imprime `eco: b'hola\n'`, el puerto está vivo. Si sale vacío, el problema es
la configuración, no el ESP32 — no sigas hasta arreglarlo.

### Cableado (igual que en la Pi 4)

| Pi 5 | ESP32 |
|---|---|
| GPIO14 / TX — pin 8 | GPIO16 (RX) |
| GPIO15 / RX — pin 10 | GPIO17 (TX) |
| GND — pin 6 | GND |

**La masa común entre Pi, ESP32 y VESC sigue siendo crítica.** Según el
`ESTADO_PROYECTO.md`, la mayoría de los problemas que tuvieron fueron cables
flojos, no software.

### Permisos

```bash
sudo usermod -aG dialout $USER
```

Cerrá sesión y volvé a entrar (o reiniciá) para que tome efecto.

---

## 2. GPIO — RPi.GPIO ya no sirve

La Pi 5 mueve el GPIO al chip **RP1**, y `RPi.GPIO` accede a registros del
BCM2835 que ya no existen. Cualquier código con `import RPi.GPIO` falla.

**El proyecto usa `gpiozero`, así que estás bien** — pero `gpiozero` necesita una
fábrica de pines que hable con el RP1:

```bash
sudo apt install -y python3-lgpio
```

Hay un problema conocido de compatibilidad: según la combinación de versiones de
kernel y `gpiozero`, la librería busca el `gpiochip4` cuando en los kernels
nuevos el RP1 quedó en `gpiochip0`, y falla al crear el `Button`.

Si te pasa, forzá la fábrica:

```bash
export GPIOZERO_PIN_FACTORY=lgpio
```

> **No es bloqueante.** Los botones ahora son solo para el operador (saltear
> estado y cancelar), no para el cliente. `pi_gui_gpio.py` los envuelve en un
> `try`: si el GPIO falla, el robot arranca igual y te lo dice por consola, y
> podés usar Enter/Backspace desde el teclado.

### Entorno virtual

Bookworm no deja instalar con `pip` en el Python del sistema (PEP 668). Si creás
el entorno **sin** acceso a los paquetes del sistema, `gpiozero` y `lgpio` no se
van a ver:

```bash
python3 -m venv --system-site-packages ~/mi_proyecto_env
```

Esa bandera es la diferencia entre que los botones funcionen o no.

---

## 3. La pantalla — la mala noticia

**El panel Tontec 3.5" SPI no es viable en la Pi 5.** Conviene saberlo ahora y no
la noche antes de la demo:

- El driver `fbtft` (el de `dtoverlay=mz61581` y `dtoverlay=piscreen`) fue
  **eliminado de los kernels modernos**.
- `fbcp-ili9341`, el puente que se usaba en la Pi 4, **no funciona en la Pi 5**:
  accede directo a registros del BCM2835 y usa DispmanX, y ninguna de las dos
  cosas existe en la Pi 5.
- El driver moderno (`panel-mipi-dbi-spi`, DRM/KMS) sí está soportado, pero **no
  se lleva bien con los ILI9486/ILI9488** de estos paneles: el código MIPI no
  maneja RGB666.

`SETUP_PI_IOT.md` ya avisaba que este era "el único paso del montaje que pelea".
En la Pi 5 directamente no pelea: pierde.

### Qué usar en su lugar

| Opción | Por qué |
|---|---|
| **Pantalla HDMI chica** ⭐ | Funciona sin tocar nada. La Pi 5 tiene dos **micro**-HDMI: hace falta el cable o adaptador |
| **Raspberry Pi Touch Display 2** (DSI) | Oficial, soportada de fábrica en la Pi 5, sin overlays ni drivers |
| Panel SPI | Solo si te sobra tiempo y ganas de pelear con device trees |

### Adaptar la resolución: son dos números

La GUI se dibuja en un lienzo virtual de 1280×720 y se escala a lo que diga la
configuración. **No hay que tocar código.** En `config.json`:

```json
"pantalla": { "ancho": 1280, "alto": 720, "fullscreen": true }
```

Para la Touch Display 2 (720×1280 en vertical) o cualquier HDMI, poné la
resolución real del panel y listo.

> Detalle: si `ancho`/`alto` son 1280×720, la GUI corre a 60 fps; con cualquier
> otra resolución baja a 30, porque asume un panel lento. En la Pi 5 con HDMI
> podés dejar 1280×720 y aprovechar los 60.

---

## 4. Alimentación — importa más de lo que parece en un robot

La Pi 5 pide **5V / 5A** por USB-C con Power Delivery. Si no detecta un
suministro de 5A al arrancar, **limita la corriente total de los puertos USB a
600 mA** (con PD de 5A sube a 1.6 A).

En un robot alimentado por batería esto se traduce en:

- El convertidor reductor (buck) tiene que dar **5A reales a 5V**, no 3A. Uno de
  3A parece funcionar hasta que la CPU acelera y la Pi se reinicia sola a mitad
  de una demo.
- Como un buck no negocia PD, la Pi asume 3A. Si necesitás los USB, forzalo en
  `/boot/firmware/config.txt`:

```
usb_max_current_enable=1
```

  Poné eso **solo** si tu fuente de verdad aguanta la corriente.

- **Refrigeración activa obligatoria.** El ventilador oficial o un disipador con
  ventilador. Dentro de la carcasa de un robot, sin ventilación, la Pi 5 se
  estrangula por temperatura y los fps de la GUI se caen.
- Verificá que no haya subtensión mientras probás:

```bash
vcgencmd get_throttled
```

  `throttled=0x0` es lo que querés ver. Cualquier otra cosa = problema de
  alimentación o de temperatura.

- La Pi 5 tiene **botón de encendido**: apagala con `sudo shutdown -h now` o el
  botón, no cortando la batería. Cortar la alimentación de golpe corrompe la SD.

---

## 5. Instalación completa, de cero

```bash
sudo apt update && sudo apt install -y python3-lgpio python3-venv git
```

```bash
python3 -m venv --system-site-packages ~/mi_proyecto_env
```

```bash
source ~/mi_proyecto_env/bin/activate && pip install pygame numpy pyserial pyzmq opencv-python-headless qrcode
```

`qrcode` es la dependencia nueva (el QR de sesión que se dibuja en pantalla). Va
**sin** `[pil]`: solo se usa para calcular la matriz de módulos y se dibuja con
Pygame, así que no hace falta Pillow. Si falta, el robot no se cae — muestra la
URL en texto.

**La Pi no lleva MediaPipe:** la visión sigue corriendo en la PC.

Después, el UART (§1) y a reiniciar.

---

## 5b. Probar HOY, con solo la pantalla y la Foscam

No hace falta el ESP32 ni las ruedas para validar casi todo. De los siete
estados del robot, **el único que necesita motores es el acercamiento**.

```bash
ROBOT_SIN_MOTORES=1 python3 ~/robot_bipedo/pi/pi_gui_gpio.py
```

Con esa variable, al detectar a una persona el robot **saltea el acercamiento** y
pasa directo a mostrar el QR. Sin ella se quedaría trabado ahí: como no puede
avanzar, la persona nunca se ve más grande y el lazo espera hasta el timeout sin
llegar nunca a la invitación.

En el pie de la pantalla vas a leer `● modo banco (sin ruedas)`.

**Lo que sí podés validar así, de punta a punta:**

| | |
|---|---|
| Foscam → Pi → PC | los frames llegan y la PC devuelve detecciones |
| Detección de personas | y de paso calibrás `alto_objetivo` (§ mirá los logs) |
| El pingüino | parpadea, sigue a la persona con los ojos, cambia de humor |
| El QR de sesión | se genera y se escanea de verdad |
| La WebApp en el celular | cédula, Módulo 10, catálogo, joystick, compra |
| El registro de la venta | SQLite en la PC + el dashboard + Firebase |
| Los timeouts | el robot no se traba si el cliente se va |

Manejando desde el celular vas a ver **las barras de avance y giro moverse en la
pantalla del robot** y al pingüino reaccionar, aunque no haya ruedas. Es la mejor
forma de comprobar que la cadena celular → PC → Pi funciona antes de sumarle
motores.

**Lo único que queda sin probar** es lo que toca las ruedas: el lazo de
acercamiento, los giros, el pivote y el baile. Todo eso se prueba después con
`python3 pi/pi_uart.py`, **con las ruedas en el aire**.

> Aviso: sin el ESP32 conectado no se puede probar el **hombre muerto**, que es
> el seguro que frena el robot si se corta el WiFi mientras alguien maneja. Esa
> prueba (§6) es obligatoria antes de dejar que un cliente maneje con las ruedas
> en el piso.

---

## 6. Verificación, en orden

Hacelo en este orden. Cada paso depende del anterior.

```bash
cat /proc/device-tree/model
```
→ debe decir `Raspberry Pi 5 Model B`.

```bash
ls -l /dev/ttyAMA0
```
→ tiene que existir. Si no, falta `dtoverlay=uart0-pi5` (§1).

```bash
vcgencmd get_throttled
```
→ `throttled=0x0`. Si no, alimentación o temperatura (§4).

```bash
ping -c 3 <ip-de-la-pc>
```
→ si falla, la red tiene aislamiento de clientes o el firewall de Windows está
bloqueando. Está todo en `SETUP_PI_IOT.md`, bloques B y C.

Con la PC ya corriendo (`py robot_server.py` desde `pc\`):

```bash
source ~/mi_proyecto_env/bin/activate && python3 ~/robot_bipedo/pi/pi_uart.py
```

Esa prueba mueve el robot: **ruedas en el aire y kill switch a mano.** Al final
corre sola la prueba del hombre muerto, que es la que no se puede saltear antes
de dejar que alguien maneje desde el celular.

```bash
ROBOT_PC_IP=192.168.43.57 python3 ~/robot_bipedo/pi/pi_gui_gpio.py
```

En el pie de la pantalla vas a ver si la PC responde y si el ESP32 está
conectado. Si algo está en rojo, se ve **antes** de que llegue el primer cliente.

---

## 7. Lo que NO cambia

- **El firmware del ESP32**: idéntico. Habla UART con la Pi, le da igual qué Pi sea.
- **La PC**: idéntica. Visión, base de datos, WebApp del celular.
- **`config.json`**: el puerto ya dice `/dev/ttyAMA0`, que es correcto en las dos.
  Solo vas a tocar `pantalla.ancho`/`alto` si cambiás de panel.
- **La Foscam por cable en `192.168.50.x`**: igual (`SETUP_PI_IOT.md`, bloque A).
- **Los pines de los botones** (5, 6, 13, 26): siguen libres. Se eligieron para
  esquivar el panel SPI; como en la Pi 5 no vas a usar ese panel, te sobran pines.

---

## 8. Un bonus que te dio la Pi 5

La Pi 5 es unas 2-3 veces más rápida que la Pi 4. Eso abre una puerta que antes
estaba cerrada: **la detección de personas podría correr en la propia Pi**, sin
depender de la PC ni del hotspot.

**No lo cambiaría por ahora**, y por dos razones: tu PC sigue siendo bastante más
rápida, y la arquitectura actual ya está probada de punta a punta. Pero si algún
día querés que el robot funcione sin laptop, el camino es:

1. `pip install mediapipe` en la Pi.
2. Correr `pc_server.py` en la Pi apuntando `ROBOT_PC_IP` a `127.0.0.1`.

El código no distingue: la Pi ya le habla al servidor de visión por ZeroMQ, y
`127.0.0.1` es una dirección como cualquier otra. Lo que **sí** seguiría en la PC
es la base de datos y la WebApp del celular, porque el celular necesita llegar a
alguien por WiFi.

---

## Si algo falla

| Síntoma | Causa casi segura |
|---|---|
| `[UART] El puerto /dev/ttyAMA0 no existe` | Falta `dtoverlay=uart0-pi5` (§1) |
| El puerto abre pero el ESP32 nunca contesta | Estás en `/dev/serial0` = conector de depuración (§1), o falta la masa común |
| `Permission denied` en el puerto | Falta `usermod -aG dialout` y volver a entrar (§1) |
| El robot arranca pero los botones no responden | Fábrica de pines de gpiozero (§2). No es bloqueante |
| La Pi se reinicia sola bajo carga | Buck de 3A en vez de 5A (§4) |
| La GUI va a tirones | Sin refrigeración activa: `vcgencmd get_throttled` (§4) |
| La pantalla SPI no muestra nada | No va a andar en la Pi 5 (§3). Pasate a HDMI o DSI |

---

**Fuentes de los cambios de la Pi 5:**
[overlay uart0-pi5 en el foro oficial](https://forums.raspberrypi.com/viewtopic.php?t=378931) ·
[mapeo de UARTs de la Pi 5 (respuesta de PhilE, ingeniero de Raspberry Pi)](https://forums.raspberrypi.com/viewtopic.php?t=359132) ·
[README de overlays de raspberrypi/firmware](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README) ·
[gpiozero: fábrica lgpio en la Pi 5](https://github.com/gpiozero/gpiozero/issues/1166) ·
[fbtft y alternativas a fbcp en la Pi 5](https://forums.raspberrypi.com/viewtopic.php?p=2304777) ·
[USB Power Delivery en la Raspberry Pi 5 (whitepaper oficial)](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009856-WP-1-USB%20Power%20delivery%20on%20Raspberry%20Pi%205.pdf)
