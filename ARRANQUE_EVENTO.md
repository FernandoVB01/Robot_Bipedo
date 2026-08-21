# Runbook del EVENTO — encender todo de 0

Pi: **`fercho@10.172.223.131`**  ·  (la IP puede cambiar con el hotspot →
si no entra, mirá la nueva con `hostname -I` en la Pi, o entrá por nombre.)

Piezas: **PC** (API + visión + dashboard) · **celular-cámara** (IP Webcam) ·
**Raspberry** (ojos + motores) · **ESP32** (por USB en la Pi).

---

## 0. PREPARACIÓN (hacelo HOY, una vez)

Pasá las últimas versiones de los archivos de la Pi (desde PowerShell en la PC):

```
scp -r "C:\Users\Joshua\Documents\Robot_Bipedo\Robot_Bipedo_TRASPASO\Robot_Bipedo\pi" fercho@10.172.223.131:~/robot_bipedo/
```

Y confirmá que el **ESP32 está flasheado** con el firmware calibrado (tu `CUENTAS_POR_MM` y `VIA_MM`, velocidad ajustada, comandos `U`/`I`).

---

## 1. Hardware (antes de encender)

- **ESP32 por USB a la Raspberry** (no a la laptop).
- VESC con batería · **GND común** ESP32↔VESC · **kill switch a mano**.
- **Monitor HDMI** conectado a la Pi (es donde salen los ojos).
- Celular-cámara enchufado, pantalla que no se apague.

---

## 2. Red (hotspot) + IPs

1. Hotspot ON. Conectá al MISMO hotspot: **PC**, **Pi** y **celular-cámara**.
2. IP de la **PC**: `ipconfig` → Wi-Fi → IPv4. **Anotala.**
3. IP de la **Pi**: la de hoy es `10.172.223.131`; si cambió, en la Pi `hostname -I`.

---

## 3. PC — servidor + (Ngrok) + dashboard

Desde `pc\`:
```
py robot_server.py
```
Esperá `[DB] Base de datos lista` y `Escuchando en tcp://*:5555`.

Ngrok (para que el QR de la encuesta abra con datos móviles):
```
ngrok http 8000
```
Anotá el link `https://....ngrok-free.dev`.

Abrí el **dashboard** (para el gestor de QR y ver los datos entrando).

---

## 4. Cámara (IP Webcam)

Celular-cámara: **IP Webcam → Iniciar servidor** → anotá `http://IP:8080`.
En `config.json` (sección `vision`): `"camara_url": "http://IP_CELULAR:8080/video"`.

---

## 5. Raspberry — VENTANA SSH 1: los OJOS

```
ssh fercho@10.172.223.131
source ~/mi_proyecto_env/bin/activate
ROBOT_URL_ENCUESTA=https://TU_LINK.ngrok-free.dev/encuesta ROBOT_PC_IP=IP_DE_LA_PC DISPLAY=:0 python3 ~/robot_bipedo/pi/interaccion_ojos.py
```
En el monitor de la Pi salen los ojos. Verificá el puntito **"visión ok"** (verde).
**Dejá esta ventana abierta.**

---

## 6. Raspberry — VENTANA SSH 2: MOVIMIENTO

Abrí OTRA ventana de PowerShell y volvé a entrar:
```
ssh fercho@10.172.223.131
source ~/mi_proyecto_env/bin/activate
ls /dev/ttyUSB*                 # confirmá el puerto (casi siempre /dev/ttyUSB0)
```

Elegí UNO (comparten el puerto USB, no van los dos a la vez):

**A) Patrulla automática** (ruedas en el aire la primera vez):
```
python3 ~/robot_bipedo/pi/recorrido.py ~/robot_bipedo/pi/recorrido.json --puerto /dev/ttyUSB0
```
(agregá `--loop 0` para que patrulle en bucle).

**B) Manejo manual desde el celular** (joystick):
```
python3 ~/robot_bipedo/pi/control_manual.py --puerto /dev/ttyUSB0
```
En el celular (hotspot): **`http://10.172.223.131:8080`** → joystick + PARAR.

> Para cambiar de A a B (o al revés): `Ctrl+C` en esta ventana y lanzá el otro.
> Los OJOS (ventana 1) siguen corriendo en paralelo sin problema.

---

## 7. Seguridad (durante el evento)

- Primer arranque de motores: **ruedas en el aire**.
- En **patrulla automática**, el PARAR del celular NO está activo → **kill switch
  físico a mano** o alguien en el teclado para `Ctrl+C`.
- En **manual (joystick)**, el PARAR del celular sí frena. Soltar el joystick frena.
- **Espacio despejado** alrededor del recorrido.

---

## Checklist rápido

1. ESP32 por USB a la Pi · VESC con batería · GND común · monitor HDMI · kill switch.
2. Hotspot ON → PC, Pi, celu-cámara. `ipconfig` (IP PC).
3. PC: `py robot_server.py` + `ngrok http 8000` + dashboard.
4. Cámara: IP Webcam → URL en `config.json`.
5. Pi ventana 1: `interaccion_ojos.py` (ojos).
6. Pi ventana 2: `recorrido.py` (auto) **o** `control_manual.py` (manual).

## Si algo falla

| Síntoma | Causa casi segura |
|---|---|
| No entra el SSH | La IP cambió → `hostname -I` en la Pi, o entrá por nombre |
| Puntito "sin visión" | La PC no responde: revisá `ROBOT_PC_IP` y que `robot_server.py` corra |
| La cámara no carga | `camara_url` con IP vieja del celular |
| El robot no se mueve | Programa corriendo en la máquina equivocada, o `ls /dev/ttyUSB*` no muestra nada (revisá el USB) |
| "no pude abrir el puerto" | Otro programa lo ocupa (recorrido y control no van juntos), o puerto equivocado |
| El QR de la encuesta no abre en el celu | Sin Ngrok y con datos móviles no llega; usá el link de Ngrok |
