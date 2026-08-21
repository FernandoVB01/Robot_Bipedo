# Empezar acá — montar el proyecto en una PC nueva

Guía de 10 minutos para dejar el proyecto andando en otra computadora.
Cuando termines, seguí por **`ESTADO_PROYECTO.md`**, que es el mapa completo.

---

## 1. Qué estás mirando

Robot diferencial interactivo. Detecta a un cliente, se le acerca, le muestra un
QR en la pantalla, y el cliente **maneja el robot desde su celular** mientras
compra. La pantalla es un pingüino animado.

Tres computadoras se reparten el trabajo:

```
Cámara ──► Raspberry Pi ──► PC ──► celular del cliente
              │              (visión + base de datos + WebApp)
              └──► ESP32 ──► VESC ──► ruedas
```

**Esta guía es para la PC.** La Raspberry se monta con `SETUP_PI5.md`.

---

## 2. Lo primero: el repositorio ya tiene historia

Si copiaste la carpeta entera (con su `.git` adentro), **ya tenés todo**: la
historia, las ramas y el trabajo sin commitear.

```bash
git status
```

Deberías ver la rama **`feature/control-celular`** y una lista de archivos
modificados y sin seguir. Eso es correcto: **el trabajo nuevo todavía no está
commiteado**, viaja en el árbol de trabajo.

Puntos de referencia por si algo sale mal:

| Referencia | Qué es |
|---|---|
| `main` → `28f281b` | El proyecto como lo dejó el equipo |
| `a56f19c` | Respaldo del montaje IoT previo (Foscam, hotspot) |
| `feature/control-celular` | Donde estás. Todo lo nuevo |

Lo primero que conviene hacer en la PC nueva es asegurar el trabajo:

```bash
git add -A && git commit -m "Capa de control por celular: sesion web, pinguino, traccion diferencial"
```

---

## 3. Python en la PC

Hace falta Python 3.10 o superior.

```bash
pip install -r requirements-pc.txt
```

Comprobá que quedó bien:

```bash
python -c "import fastapi, uvicorn, sqlalchemy, zmq, cv2, mediapipe, numpy; print('todo OK')"
```

> **Los modelos de IA ya vienen en el paquete** (`pc/hand_landmarker.task` y
> `pc/efficientdet_lite0.tflite`, unos 21 MB). No hace falta internet para la
> primera ejecución. Si algún día faltan, `pc_server.py` los vuelve a descargar
> solo.

---

## 4. Levantar la PC

```bash
cd pc
python robot_server.py
```

Levanta en un solo proceso el servidor de visión (puerto 5555) y la API +
WebApp (puerto 8000). Tenés que ver:

```
[DB] Base de datos lista: ...
[PC-SERVER] Detector de personas listo.
[PC-SERVER] Escuchando en tcp://*:5555
```

Anotá la IP que imprime: es la que va en `config.json` → `red.pc_ip`, o en la
variable `ROBOT_PC_IP` de la Raspberry.

- Dashboard: `http://localhost:8000`
- API completa: `http://localhost:8000/docs`

---

## 5. Probar sin ninguna Raspberry ni robot

Podés validar buena parte del sistema con la PC sola.

**Módulo 10 y el ciclo de sesión:**
```bash
python pc/sesion.py
```

**Prueba de humo del servidor** (simula una Raspberry mandando frames):
```bash
python pc/mock_client.py
```

**La WebApp del celular**, que es lo más vistoso: con el servidor corriendo,
abrí `http://localhost:8000/docs`, ejecutá `POST /api/sesion/nueva`, copiá la
`url` que devuelve y abrila en el navegador. Vas a poder ingresar una cédula,
manejar con el joystick y comprar, sin ningún hardware.

> Cédula de prueba válida: **1710034065**

---

## 6. Cosas del entorno que hacen perder tiempo

**Compilar el firmware del ESP32** (PlatformIO, framework ESP-IDF):

- La versión está fijada a `espressif32@5.4.0` en `esp32/platformio.ini`. **No la
  saques.** Sin fijar, PlatformIO baja ESP-IDF 6.x y la compilación falla con
  `Couldn't find target config target-__ldgen_output_sections.ld`, que es un bug
  de la integración PlatformIO/IDF6 y no tiene nada que ver con el código.
- En **Windows**, compilá en una **ruta corta** (`C:\Temp\rbfw` o parecido). El
  toolchain xtensa choca con el límite de 250 caracteres de Windows y falla con
  un error críptico del compilador.

**Los archivos de `pi/`** llevan shebang de Linux. En Windows hay que invocarlos
con `python.exe` directamente, no con el lanzador `py`.

**La IP de la PC cambia** cada vez que se reinicia el hotspot. No hace falta
editar nada: la Raspberry acepta `ROBOT_PC_IP=<ip> python3 pi/pi_gui_gpio.py`, y
el QR se arma con la dirección que la Pi ya usó para llegar, así que siempre
apunta a algo alcanzable.

---

## 7. Lo que NO viene en el paquete

| | Por qué |
|---|---|
| `pc/firebase_credentials.json` | Es una credencial. Se baja de la consola de Firebase (pasos en `pc/FIREBASE_SETUP.md`) |
| `robot_bipedo.db` | La base de datos se crea sola al arrancar |
| Contraseña del WiFi / hotspot | Nunca estuvo en el repo |

Firebase arranca **desactivado** a propósito (`config.json` → `firebase.activo:
false`). El robot funciona igual guardando en SQLite; lo que quede pendiente se
sube después con `python pc/sync_firebase.py`.

---

## 8. Qué leer, en orden

1. **`ESTADO_PROYECTO.md`** — el mapa completo: qué se hizo, qué falta, rutas,
   comandos y decisiones tomadas. **Empezá por acá.**
2. **`ARQUITECTURA_IOT.md`** — arquitectura y protocolos. El documento técnico vigente.
3. **`SETUP_PI5.md`** — montar la Raspberry Pi 5 (UART, GPIO, pantalla, alimentación).
4. **`SETUP_PI_IOT.md`** — Foscam, hotspot, firewall de Windows, checklist de laboratorio.
5. **`ROADMAP_BALANCEO.md`** — el robot autoequilibrado, que va por carril aparte.

---

## 9. La advertencia que no se puede saltear

Nada de esto se probó todavía con hardware real: no hubo ESP32, ni VESC, ni
motores conectados. Antes de dejar que alguien maneje el robot desde un celular
con las ruedas en el piso, **hay que probar el hombre muerto**:

```
MODO:TELEOP
MOV:0.3:0
```

...y dejar de mandar comandos. A los ~400 ms las ruedas tienen que frenar solas
y aparecer `EVT:DEADMAN` en el monitor. Si siguen girando, no conectes el
celular. El procedimiento está en `ARQUITECTURA_IOT.md` §4.3.

Y la regla de siempre: **todo se prueba con las ruedas en el aire y el kill
switch a mano.**
