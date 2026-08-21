# Arranque en el laboratorio — levantar todo el sistema

Guía para dejar el robot andando cada vez que llegás. **Lo único que cambia entre
sesiones son las IPs** (la del hotspot cambia siempre). El resto es igual.

Piezas: **PC** (visión + API + dashboard + base de datos) · **celular-cámara**
(IP Webcam) · **Raspberry Pi** (ojos + gestos) · **ESP32** (motores / recorrido).

---

## Paso 1 — Red (hotspot) e IP de la PC

1. Prendé el **hotspot del celular**. Conectá al mismo hotspot: la **PC**, la
   **Raspberry**, y el **celular-cámara**.
2. En la PC, sacá su IP: `ipconfig` → adaptador **Wi-Fi** → **Dirección IPv4**
   (ej. `192.168.43.50`). **Anotala: la vas a usar en todos lados.**

---

## Paso 2 — PC: levantar el servidor (API + visión + dashboard)

En una terminal, en la carpeta **`pc\`** del proyecto:

```powershell
py robot_server.py
```

Tenés que ver:
- `[DB] Base de datos lista: ...`
- `[PC-SERVER] ... Escuchando en tcp://*:5555`
- la API en `http://localhost:8000`

> PC nueva (una vez): `pip install fastapi uvicorn sqlalchemy pyzmq opencv-python mediapipe qrcode`

---

## Paso 3 — Cámara: celular con IP Webcam

1. En el celular-cámara: abrí **IP Webcam** → **Iniciar servidor** → anotá la URL
   (`http://192.168.x.x:8080`). **Esa IP también cambió.**
2. En **`config.json`** (sección `vision`), actualizá:
   ```jsonc
   "camara_url": "http://IP_DEL_CELULAR:8080/video"
   ```
3. Tené el teléfono **enchufado** y con la pantalla que no se apague.

---

## Paso 4 — Dashboard (verificar que la API responde)

Abrí el dashboard en la PC:
- `http://localhost:8000/` (el que sirve la PC), o tu **dashboard lindo** (que
  habla con `http://localhost:8000`).
- Deberías ver métricas y, en **Gestor QR**, poder crear/ver QR.

---

## Paso 5 — Raspberry Pi: los ojos / interacción

Por SSH desde la PC (por nombre, así no importa la IP):

```bash
ssh USUARIO@NOMBRE.local
source ~/mi_proyecto_env/bin/activate
ROBOT_PC_IP=IP_DE_LA_PC DISPLAY=:0 python3 ~/robot_bipedo/pi/interaccion_ojos.py
```

- El monitor HDMI de la Pi muestra los ojos. Verificá el puntito **"visión ok"**
  (verde) y que despierten al ponerte frente a la cámara.
- Teclas de prueba sin cámara: **1** = estado datos, **2** = estado oferta.

> Si cambiaste código en la PC, pasalo antes a la Pi:
> `scp -r "...\Robot_Bipedo_TRASPASO\Robot_Bipedo\pi" USUARIO@NOMBRE.local:~/robot_bipedo/`

---

## Paso 6 — ESP32: movimiento y recorrido

1. Enchufá el **USB del ESP32** (a la laptop o a la Pi). **Cerrá el monitor serie**
   antes de correr el recorrido (el puerto no lo pueden usar dos programas).
2. Puerto: en Windows es `COMx` (mirá el Administrador de dispositivos); en la Pi
   es `/dev/ttyUSB0`.
3. Manejo manual / calibración (monitor serie): `w/a/s/d`, `x`, `F<cm>`, `G<grados>`, `r`.
4. Recorrido automático (**ruedas en el aire la primera vez**):
   ```
   python recorrido.py recorrido.json --puerto COM3        # laptop
   python3 recorrido.py recorrido.json --puerto /dev/ttyUSB0  # Pi
   ```
   Patrullar en loop: agregá `--loop 0`. `Ctrl+C` frena y sale.

---

## Checklist rápido (orden de encendido)

1. Hotspot ON → PC, Pi y celular-cámara conectados.
2. `ipconfig` → anotar IP de la PC.
3. PC: `py robot_server.py` (desde `pc\`).
4. Celular-cámara: IP Webcam → server → poner la URL en `config.json`.
5. Pi (SSH): `interaccion_ojos.py` con `ROBOT_PC_IP` y `DISPLAY=:0`.
6. ESP32 por USB → `recorrido.py` (o manejo manual).

---

## Si algo falla

| Síntoma | Causa casi segura |
|---|---|
| Puntito "sin visión" en la Pi | La PC no responde: revisá IP (`ROBOT_PC_IP`) y que `robot_server.py` corra |
| La cámara no carga | `camara_url` con IP vieja del celular, o IP Webcam apagada |
| Dashboard sin datos | `robot_server.py` no está corriendo, o abriste el dashboard sin el servidor |
| `recorrido.py` no abre el puerto | Puerto equivocado, o el monitor serie está abierto ocupándolo |
| El QR de la encuesta no abre en el celular | El celular tiene que estar en el hotspot; o poné la URL de Ngrok si usa datos móviles |
