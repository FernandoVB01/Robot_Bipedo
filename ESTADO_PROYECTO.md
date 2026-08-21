# Estado del proyecto — Robot Pingüino

Dónde estamos, qué funciona, qué falta y dónde está cada cosa.
**Este es el documento para retomar el trabajo**: si abrís un chat nuevo o volvés
después de un tiempo, empezá por acá.

Última actualización: **11 de agosto de 2026**

---

## 1. El proyecto en una frase

Robot diferencial interactivo para promociones comerciales. Detecta a un cliente,
se le acerca, le muestra un QR en la pantalla, y el cliente **maneja el robot
desde su celular** mientras compra. La pantalla es un pingüino animado (Pingüi).

Tiene **dos grandes partes que avanzan por separado**:

- **Sistema interactivo** (cámara, QR, celular, base de datos) → **el foco actual**
- **Robot autoequilibrado** (mantenerse erguido sobre 2 ruedas) → ver `ROADMAP_BALANCEO.md`

---

## 2. Dónde estamos AHORA MISMO

**Rama de trabajo: `feature/control-celular`.**
`main` sigue intacto en `28f281b`. El trabajo previo del equipo quedó preservado
en el commit `a56f19c` como punto de restauración.

> ⚠️ **Nada de lo nuevo está commiteado todavía.** Está en el working tree.

**Hardware conectado hoy:** Raspberry Pi + pantalla + cámara Foscam.
**Sin conectar:** ESP32, VESC, motores.

### Qué se puede probar hoy, sin ESP32

```bash
ROBOT_SIN_MOTORES=1 python3 ~/robot_bipedo/pi/pi_gui_gpio.py
```

Corre el flujo completo del cliente salteando el acercamiento (el único estado
que necesita ruedas). Detalle en `SETUP_PI5.md` §5b.

---

## 3. Lo que se construyó (y está verificado)

### ESP32 — `esp32/main/main.c`

Reescritura mayor, **compatible hacia atrás** (todos los comandos viejos siguen
funcionando). Compila limpio: **RAM 2.9%, Flash 24.2%**.

- **Tracción diferencial de verdad**: `vesc_set_duty_lr(izq, der)`. Antes se
  mandaba el mismo duty a las dos ruedas, así que el robot no podía girar.
- **Máquina de movimiento no bloqueante** (tick de 20 ms). Antes `AVANZAR_T` y
  `GIRAR_180` corrían un `while` que dejaba sorda la tarea de control.
- **Freno activo** con `COMM_SET_CURRENT_BRAKE` en vez de duty=0 (rueda libre).
- **Odometría**: lee la VESC con `COMM_GET_VALUES` y saca el tacómetro.
- **`GIRO:<grados>` en lazo cerrado**, con respaldo por tiempo si la VESC no contesta.
- **`PIVOTE`** (arco pivotando sobre una rueda), **`RUTINA:BAILE/SALUDO/GIRO_180`**.
- **Hombre muerto**: 400 ms sin `MOV:` en teleoperación → freno.
- **`CAL:` + NVS**: calibrar por UART sin reflashear.
- **Telemetría** `TEL:` a 5 Hz y eventos `EVT:`.

### PC

| Archivo | Qué es |
|---|---|
| `pc/sesion.py` | **NUEVO.** Store de sesiones + validación Módulo 10 |
| `pc/api_server.py` | Endpoints de sesión + sirve la WebApp |
| `pc/static/control.html` | **NUEVO.** WebApp del celular, 100% autocontenida |
| `pc/pc_server.py` | Detección de personas (EfficientDet-Lite0) |

### Raspberry Pi

| Archivo | Qué es |
|---|---|
| `pi/pinguino.py` | **NUEVO.** Pingüi: ojos, humores, baile. Primitivas de Pygame |
| `pi/pi_sesion_client.py` | **NUEVO.** Sondeo a 20 Hz con conexión persistente |
| `pi/pi_qr.py` | **NUEVO.** QR de sesión en pantalla |
| `pi/pi_acercamiento.py` | **NUEVO.** Lazo que lleva al robot hasta la persona |
| `pi/pi_uart.py` | Comandos nuevos, `send_fast`, hilo lector, diagnóstico Pi 5 |
| `pi/pi_gui_gpio.py` | Máquina de estados nueva, sin cédula por GPIO, modo banco |

### Documentación

| Archivo | Qué es |
|---|---|
| `ARQUITECTURA_IOT.md` | **NUEVO.** Arquitectura + protocolos. **El documento vigente** |
| `SETUP_PI5.md` | **NUEVO.** Raspberry Pi 5: UART, GPIO, pantalla, alimentación |
| `SETUP_PI_IOT.md` | Montaje (Foscam, hotspot, firewall). Con avisos para Pi 5 |
| `DOCUMENTACION_PROYECTO.md` | El primer avance. Parcialmente superado |
| `ROADMAP_BALANCEO.md` | El robot autoequilibrado, sin cambios |

---

## 4. Problemas que se encontraron en el código que ya existía

Vale la pena tenerlos presentes: casi todos estaban ocultos.

1. **No había tracción diferencial.** El robot solo podía ir derecho o de reversa.
2. **Los movimientos temporizados bloqueaban** la tarea de control.
3. **El firmware nunca leía la VESC** → de ahí el giro de 90° que daba 106-110°.
4. **La validación Módulo 10 nunca existió.** `DOCUMENTACION_PROYECTO.md` y
   `config.json` decían que estaba en `pi_gui_gpio.py`; ahí solo se armaban 10
   dígitos y se mandaban sin comprobar nada.
5. **La cámara y el UART compartían un `import`**: sin `pyserial`, el robot se
   quedaba también sin cámara.
6. **PlatformIO sin versión fijada** bajaba ESP-IDF 6.x y la compilación fallaba
   antes de tocar el código del proyecto.
7. **Los modelos de MediaPipe no estaban en `.gitignore`** (7.8 MB ya commiteados,
   14 MB más en camino).

Y tres que aparecieron al probar lo nuevo, ya corregidos:

8. Al terminar el baile en teleoperación, **el joystick quedaba muerto** para siempre.
9. Los comandos con ACK **congelaban la pantalla** hasta 3 s si el UART fallaba.
10. La cara del pingüino **volvía a "atento" mientras el robot seguía bailando**.

---

## 5. Lo que falta — por orden de prioridad

### 🔴 Bloqueantes para que el robot se mueva

1. **Conectar el ESP32 y configurar el UART de la Pi 5.**
   `dtoverlay=uart0-pi5` en `/boot/firmware/config.txt`. **No** `disable-bt`.
   Ojo: en la Pi 5, `/dev/serial0` es el conector de depuración, no los pines 8/10.
   → `SETUP_PI5.md` §1

2. **Comprobar el sentido de giro de las ruedas.**
   `MOV:0.2:0` tiene que avanzar. Si gira, hay que poner `MOTOR_DER_SIGNO` en
   `-1.0f` — es lo **único** que obliga a recompilar.
   → `esp32/main/main.c`, sección de pines

3. **Probar el hombre muerto. NO SALTEAR.**
   `MODO:TELEOP`, después `MOV:0.3:0`, y dejar de mandar. A los ~400 ms tiene que
   frenar y aparecer `EVT:DEADMAN`. Si no frena, no conectar el celular.
   → `ARQUITECTURA_IOT.md` §4.3

4. **Calibrar el giro** (el problema de los 106-110°).
   `CAL:ms_grado:<valor>` y `CAL:guardar:1`. Fórmula y procedimiento completo al
   final de `esp32/main/main.c`.

5. **Calibrar `alto_objetivo`** del acercamiento: pararse donde querés que el
   robot frene y copiar el número que imprime `[ACERCAMIENTO]`.
   → `config.json` → `acercamiento`

### 🟡 Decisiones pendientes

6. **La pantalla.** El panel Tontec SPI **no funciona en la Pi 5** (`fbtft` salió
   de los kernels y `fbcp-ili9341` no corre ahí). Hay que decidir entre HDMI o la
   Touch Display 2 por DSI. Son dos números en `config.json`, cero código.
   → `SETUP_PI5.md` §3

7. **Alimentación.** La Pi 5 pide 5V/**5A**. Un buck de 3A la reinicia bajo carga.
   Y necesita refrigeración activa. → `SETUP_PI5.md` §4

8. **Verificar los offsets del tacómetro de la VESC.** El parser asume firmware
   VESC 5.x/6.x. Si la VESC es más vieja, el código lo detecta y avisa, y los
   giros caen al modo por tiempo. Se ve en el flag `O` de la telemetría.

### 🟢 Sin verificar todavía

9. **Nada se probó con hardware real.** Ni ESP32, ni VESC, ni Foscam.
10. **El pingüino no se vio en Pygame real** (no está instalado en la PC). Se
    verificó rasterizando el código real con OpenCV. Confirmar con
    `python3 pi/pinguino.py` en la Pi.
11. **Commitear todo.** Sigue en el working tree.

---

## 6. Cómo levantar el sistema

**Orden: primero la PC, después la Pi.**

```powershell
py robot_server.py
```
Desde `pc\`. Levanta visión (:5555) y API + WebApp (:8000).

```bash
ROBOT_PC_IP=192.168.43.57 python3 ~/robot_bipedo/pi/pi_gui_gpio.py
```

Dashboard: `http://<ip-de-la-pc>:8000` · API: `/docs`

En el pie de la pantalla del robot se ve si la PC responde y si el ESP32 está
conectado. **Si algo está en rojo, se ve antes de que llegue el primer cliente.**

### Pruebas sueltas, sin levantar todo

| Comando | Qué prueba |
|---|---|
| `python pc/sesion.py` | Módulo 10 y el ciclo de sesión |
| `python3 pi/pinguino.py` | Los humores del pingüino (ventana) |
| `python3 pi/pi_qr.py` | Que el QR se escanee al tamaño del panel |
| `python3 pi/pi_acercamiento.py` | El lazo de acercamiento, simulado |
| `python3 pi/pi_uart.py` | El ESP32. **Ruedas en el aire** |
| `python3 pi/pi_sesion_client.py <ip-pc>` | La cadena celular → PC → Pi |
| `py pc/mock_client.py` | Humo de la PC sin cámara ni ESP32 |

---

## 7. Entorno — cosas que hacen perder tiempo

**Repo local:** `C:\Users\luisf\Documents\Robot_Bipedo`
(el Documents **local**, no el de OneDrive — ahí vive el otro proyecto, el LQR).

**Compilar el firmware en Windows:**

```powershell
$env:PLATFORMIO_CORE_DIR = "C:\Users\luisf\Documents\Codex\.pio-codex-c"
```

- La versión está fijada a `espressif32@5.4.0` en `platformio.ini`. Sin fijar,
  PlatformIO baja IDF 6.x y falla con `Couldn't find target config
  target-__ldgen_output_sections.ld` — es un bug de la integración, no del código.
- **Compilar en una ruta corta** (`%TEMP%\rbfw`): el toolchain xtensa choca con el
  límite de 250 caracteres de Windows.

**Python en la PC:** falta `fastapi`, `uvicorn`, `pygame`, `pyserial`, `pyzmq` y
`qrcode`. Sí están `mediapipe`, `cv2` y `numpy`. O sea: **el proyecto no se puede
ejecutar en la PC tal cual**, solo compilar el firmware y probar módulos sueltos.

```powershell
pip install fastapi uvicorn sqlalchemy pyzmq opencv-python mediapipe
```

Los archivos de `pi/` llevan shebang de Linux: en Windows hay que invocarlos con
`python.exe` directamente, no con `py`.

**En la Raspberry:**

```bash
pip install pygame numpy pyserial pyzmq opencv-python-headless gpiozero qrcode
```

`qrcode` es la dependencia nueva. Va **sin** `[pil]`.

---

## 8. Decisiones tomadas (para no rediscutirlas)

| Decisión | Por qué |
|---|---|
| La "base de datos" del control vive en **la PC**, no en Firebase | 30-50 ms contra 200-500 ms. Al soltar el acelerador, el robot frena |
| Firebase queda **solo para las ventas** | Es persistencia, no control |
| La cédula se ingresa **solo en el celular** | 10 dígitos con 4 pulsadores era casi un minuto |
| Los botones GPIO quedan **para el operador** | Saltear estado y cancelar. Ya estaban cableados |
| El pingüino se dibuja con **primitivas**, no sprites | Sin archivos que copiar, escala a cualquier panel |
| La visión sigue en **la PC** | La Pi 5 podría, pero la PC es más rápida y ya está probado |
| El protocolo UART sigue siendo **texto** | Se depura con un monitor serie mientras se calibra |
| `MOV:` **no lleva ACK** | A 10 Hz, el ida y vuelta entraría en el lazo del joystick |

---

## 9. Hardware

- Raspberry Pi (**5**) + pantalla + Foscam FI8918W por cable (`192.168.50.x`)
- **ESP32** — PlatformIO, framework ESP-IDF (C nativo, no Arduino)
- **VESC dual Autoro AESC DV6.7** — maestra **42**, esclava **25** por CAN
- 2 motores hub (rueda de 168 mm → radio 84 mm)
- Conexiones ESP32: GPIO16/17 ↔ UART de la Pi · GPIO4/5 ↔ UART de la VESC
- **Masa común entre Pi, ESP32 y VESC: crítica.** La mayoría de los problemas de
  la presentación fueron cables flojos, no software.

---

## 10. Documentos, en orden de utilidad

1. **`ARQUITECTURA_IOT.md`** — arquitectura, protocolos, el análisis del giro. El vigente.
2. **`SETUP_PI5.md`** — todo lo específico de la Pi 5 y el modo banco.
3. **`SETUP_PI_IOT.md`** — Foscam, hotspot, firewall, checklist de laboratorio.
4. **`ROADMAP_BALANCEO.md`** — el robot autoequilibrado (carril aparte).
5. **`DOCUMENTACION_PROYECTO.md`** — el primer avance. Parcialmente superado.

Repo: github.com/FernandoVB01/Robot_Bipedo
