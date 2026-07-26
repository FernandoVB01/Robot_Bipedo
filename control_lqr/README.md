# Robot Bípedo Auto-balanceado — Modelado, Control LQR e Implementación

Robot de dos ruedas tipo péndulo invertido móvil. El chasis tiene una **rodilla
fija con pasador**, así que se comporta como un solo sólido rígido irregular con
el centro de masa desplazado: por eso su postura de equilibrio **no es vertical**,
sino reclinada un ángulo γ.

Este repositorio cubre el **modelado, el diseño de control y la implementación en
hardware**. La capa de aplicación del robot (visión, QR, cédula, base de datos)
vive en el repositorio del equipo:
<https://github.com/FernandoVB01/Robot_Bipedo>.

---

## 1. Convenciones (leer antes de tocar nada)

Los signos son la fuente #1 de errores en un balancín. Estas son las
convenciones que usa **todo** el proyecto:

| Símbolo | Significado |
|---|---|
| `θ > 0` | El chasis se inclina hacia **adelante** (+x) |
| `γ = atan2(x_com, y_com)` | Desfase estático por la rodilla. Equilibrio real: `θ_eq = −γ` |
| `δθ = θ + γ` | Variable que el control lleva a cero |
| `u` | Torque **total** (cada motor recibe `u/2`) |
| `Mw`, `Iw` | Masa e inercia **totales de ambas ruedas** |

Ejes según REP-103: **x adelante, y izquierda, z arriba**.

---

## 2. Estructura

```
Robot_Bipedo_LQR/
├── matlab/                       Modelado y diseño de control
│   ├── fase1_modelado.m            Euler-Lagrange simbólico → A, B, C, D
│   ├── fase2_lqr.m                 LQR continuo, c2d 100 Hz, dlqr
│   ├── fase2_build_simulink.m      Construye y simula el lazo cerrado
│   ├── fase2_analisis.m            Márgenes, Bode, controlabilidad/observabilidad
│   ├── fase3_plot_ocho.m           Grafica la misión ejecutada en Gazebo
│   ├── fase4_barrido_rodilla.m     Qué ángulo de rodilla conviene
│   └── prueba_imu_esp32/           Arnés de captura de la IMU por puerto serie
├── simulink/guia_simulink.md     Armado manual del modelo (alternativa al script)
├── ros2_ws/src/balancing_robot/  Simulación física (ROS 2 Humble + Gazebo)
├── firmware/esp32_balance/       Firmware de balanceo (ESP-IDF, C puro)
├── raspberry/                    Visor de telemetría en tiempo real (Pygame)
└── ANALISIS.md                   Análisis completo con resultados y justificación
```

---

## 3. Arquitectura del robot real

**La regla que no se rompe: la Raspberry nunca está dentro del lazo de control.**
Linux con interfaz gráfica tiene jitter de decenas de milisegundos y esta planta
se cae en ~113 ms.

```
LAZO DE CONTROL (cerrado, determinista, todo dentro del ESP32)
  MPU6050 ──I2C──► ESP32 ──UART2──► VESC maestra ──CAN──► VESC esclava
                    ▲  │  200–500 Hz
                    └──┘
TELEMETRÍA (una vía, ~50 Hz, se puede perder sin consecuencia)
  ESP32 ──UART1──► Raspberry Pi ──► gráfica en pantalla + log CSV
COMANDOS (alto nivel, esporádicos)
  Raspberry Pi ──UART1──► ESP32     "AVANZAR" / "TELE_ON" / "STOP"
```

---

## 4. Cómo operar cada parte

### 4.1 MATLAB — modelado y diseño de control

Requiere MATLAB con Symbolic Math Toolbox y Control System Toolbox.

```matlab
cd matlab
fase1_modelado        % genera modelo_robot.mat  (A, B, C, D, L_eff, gamma)
fase2_lqr             % genera lqr_design.mat    (K continua y Kd discreta)
fase2_build_simulink  % construye el .slx, simula y guarda la gráfica
fase2_analisis        % márgenes de estabilidad y observabilidad
```

El orden importa: cada script consume el `.mat` del anterior.

Para abrir el modelo de Simulink a mano, primero carga las variables al
workspace (los bloques las leen de ahí):

```matlab
load lqr_design.mat
open_system('fase2_lazo_cerrado')
```

**Al cambiar los parámetros del robot** (masas, inercias, COM, radio de rueda)
se editan en `p_vals` dentro de `fase1_modelado.m` y se vuelve a correr la
cadena completa.

### 4.2 Elegir el ángulo de la rodilla

```matlab
fase4_barrido_rodilla
```

Barre el ángulo de rodilla y, para cada uno, recalcula el COM, la inercia, γ y
el LQR, y evalúa métricas de control (velocidad de caída, torque pico,
inclinación máxima recuperable). Primero pon **tu** geometría real en la
cabecera del script.

### 4.3 Simulación física — ROS 2 Humble + Gazebo

Requiere Ubuntu 22.04 (nativo o WSL2). Instalación asistida:

```bash
bash ros2_ws/setup_wsl.sh
```

Lanzar la misión completa (recta → ocho → recta):

```bash
ros2 launch balancing_robot gazebo.launch.py
```

Recomendado en WSL, desde otra terminal, para evitar congelones que tumban el
robot:

```bash
sudo chrt -f -p 50 $(pgrep gzserver | head -1)
```

Repetir la misión sin reiniciar Gazebo, con parámetros:

```bash
ros2 run balancing_robot trajectory_generator --ros-args -p v_crucero:=0.2 -p w_giro:=0.4
```

### 4.4 Firmware del ESP32

Requiere PlatformIO. Desde `firmware/esp32_balance`:

```bash
pio run              # compilar
pio run -t upload    # flashear (la ESP32 debe estar en la PC por USB)
pio device monitor   # consola
```

Antes de confiar en el control hay que verificar cuatro números en
`src/config.h` — están explicados en `firmware/esp32_balance/README.md`:
`PITCH_SIGN`, `KT_NM_PER_A`, `MOTOR_POLE_PAIRS` y `RPM_OFFSET`.

La ley de control es una sola:

```
u = kth·δθ + kdth·δθ̇ + kx·(x − x_ref) + kdx·(ẋ − v_ref)
```

Con `kx = kdx = 0` **es un PID de ángulo**; activando los otros dos queda el
**LQR de 4 estados** que además regula la posición. Mismo código, activación
progresiva.

### 4.5 Visor de telemetría (Raspberry o PC)

Entiende dos formatos y los detecta solo: el CSV crudo del MPU6050 y la
telemetría del firmware de balanceo.

En la Raspberry, con la ESP32 conectada por USB:

```bash
./lanzar_visor.sh
```

En la PC:

```bash
python telemetry_viewer.py --port COM7
```

Teclas: `SHIFT+A` armar · `X` parar · `T` telemetría · `E` fijar cero ·
`C` captura · `L` log CSV · `P` pausa · `Q` salir.
Detalles y el parche necesario para `pi_uart.py`: `raspberry/README.md`.

---

## 5. Flujo completo, del modelo al robot

1. **Medir el robot** (CAD o báscula): masas, inercias, COM, radio de rueda.
2. **MATLAB**: `fase1` → `fase2` → obtienes `γ` y las ganancias `Kd`.
3. **Validar en simulación**: Simulink (respuesta a perturbación) y Gazebo
   (misión completa).
4. **Etapa A**: capturar la IMU con el visor y confirmar ejes, signo, bias y
   frecuencia reales.
5. **Firmware**: poner `PITCH_SIGN` y las ganancias, flashear.
6. **Puesta en marcha por etapas** (ver `firmware/esp32_balance/README.md`),
   siempre con las **ruedas en el aire** hasta aprobar el chequeo de signo.
7. **Comparar** la captura real contra la simulación: esa es la validación
   experimental del modelo.

---

## 6. Estado actual

| Parte | Estado |
|---|---|
| Modelo simbólico y espacio de estados | ✅ Verificado |
| LQR + análisis de estabilidad y robustez | ✅ Verificado (PM 58.6°, Ms 1.69 a 100 Hz) |
| Simulink, respuesta a perturbación | ✅ Verificado |
| ROS 2 + Gazebo, misión recta→ocho→recta | ✅ Ejecutada con éxito |
| Barrido del ángulo de rodilla | ✅ Herramienta lista (falta geometría real) |
| Visor de telemetría | ✅ Funcionando en la Raspberry, 100 Hz sin descartes |
| Firmware de balanceo | ⚙️ Compila limpio; **falta puesta en marcha sobre hardware** |
| Camino de corriente ESP32→VESC | ⏳ Pendiente (Etapa 1) |

**Importante:** los parámetros físicos actuales (masas, inercias, COM) son
**valores de ejemplo**. Las ganancias derivadas de ellos sirven de referencia,
no para flashear en el robot real.

---

## 7. Detalles que cuestan horas si no se saben

- **MATLAB**: `fase2_build_simulink` construye el modelo por código; si abres el
  `.slx` sin cargar antes `lqr_design.mat`, los bloques no encuentran las
  variables.
- **Gazebo/ROS 2**: `gazebo_ros2_control` en Humble **falla si el URDF tiene
  comentarios XML** (su parser se rompe con `: ` dentro de un comentario); el
  launch los elimina antes de publicar. Además el robot se cae mientras cargan
  los controladores, por eso el launch dispara un `reset_world` automático.
- **WSL**: los congelones de 1–3 s tumban el balanceo. Prioridad de tiempo real
  con `chrt` lo mitiga.
- **ESP32 + Raspberry**: por USB el puerto es `/dev/ttyUSB0`; por los pines GPIO
  hay que liberar `/dev/ttyAMA0`, que en la Pi 4 lo toma el Bluetooth.
- **GND común** entre Pi, ESP32 y VESC: la mayoría de los fallos de comunicación
  del equipo fueron cables sueltos, no software.
- **Un puerto serie admite un solo usuario**: cierra el monitor de PlatformIO
  antes de abrir el visor.

---

## 8. Documentación adicional

| Archivo | Contenido |
|---|---|
| `ANALISIS.md` | Análisis completo: polos, controlabilidad, observabilidad, márgenes, justificación de los 100 Hz |
| `firmware/esp32_balance/README.md` | Comandos, los 4 números a verificar y la puesta en marcha por etapas |
| `raspberry/README.md` | Uso del visor, teclas, formatos y parche para `pi_uart.py` |
| `simulink/guia_simulink.md` | Armado manual del modelo de Simulink |
