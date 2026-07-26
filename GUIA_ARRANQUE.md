# Guía de arranque — Robot Bípedo (día de la presentación)

Todo lo necesario para dejar el robot funcionando desde cero en la U.

---

## ⚠️ Lo primero: las IPs cambian en cada red

En la WiFi de la U, la PC y el celular reciben **IPs distintas** a las de tu casa.
Casi todo lo que "deja de andar" es por esto. Vas a necesitar 2 IPs nuevas:

- **IP de la PC** → va en el config de la Pi, campo `pc_ip`.
- **IP del celular** → va en el config de la Pi, campo `camara_url`.

---

## Orden de encendido (respetá el orden)

1. Conectar **PC, celular y Raspberry a la MISMA WiFi**. ESP32 + VESC alimentados.
2. Celular transmitiendo (IP Webcam).
3. PC (dos servidores).
4. Actualizar IPs en el config de la Pi.
5. Arrancar la Raspberry.

> ⚠️ **Seguridad:** el robot **arranca rodando** apenas se lanza el programa de la
> Pi. Para pruebas, ponelo con las ruedas en el aire (sobre una caja) o dejá la
> VESC apagada hasta estar listo.

---

## PASO 1 — Averiguar la IP de la PC

En la PC, abrí `cmd` y escribí:
```
ipconfig
```
Anotá la **Dirección IPv4** de la WiFi (algo como `192.168.x.x`).

## PASO 2 — Celular (cámara)

1. Conectá el celular a la misma WiFi.
2. Abrí **IP Webcam** → **Iniciar servidor**.
3. Anotá la dirección que muestra abajo: `http://192.168.x.x:8080`.
   La URL de la cámara es esa **+ `/video`** → `http://192.168.x.x:8080/video`
4. Dejá la app adelante y la pantalla encendida.

## PASO 3 — PC: dos servidores

**Ventana 1 (visión):**
```
cd C:\Users\Joshua\Documents\Robot_Bipedo\pc
py pc_server.py
```
Esperá: `[FIREBASE] Conectado` y `[PC-SERVER] Escuchando en tcp://*:5555`.

**Ventana 2 (dashboard), nueva:**
```
cd C:\Users\Joshua\Documents\Robot_Bipedo\pc
py api_server.py
```
Abrí el navegador en `http://localhost:8000`.

> Abrí los puertos del firewall si la red es nueva (cmd como administrador):
> ```
> netsh advfirewall firewall add rule name="RB5555" dir=in action=allow protocol=TCP localport=5555
> netsh advfirewall firewall add rule name="RB8000" dir=in action=allow protocol=TCP localport=8000
> ```

## PASO 4 — Actualizar las IPs en la Raspberry

Entrá a la Pi (`ssh pi@<IP_de_la_Pi>`, o con monitor y teclado) y editá el config:
```
nano /home/pi/robot_bipedo/config.json
```
Cambiá **dos** valores con las IPs de hoy:
- En `"red"` → `"pc_ip": "IP_DE_LA_PC"` (Paso 1)
- En `"vision"` → `"camara_url": "http://IP_DEL_CELU:8080/video"` (Paso 2)

Guardá con `Ctrl+O`, `Enter`, `Ctrl+X`.

> Si al guardar el programa se queja del JSON, revisá las comas. Para chequear:
> ```
> python3 -c "import json; json.load(open('/home/pi/robot_bipedo/config.json')); print('JSON OK')"
> ```

## PASO 5 — Arrancar el robot

```
source ~/mi_proyecto_env/bin/activate
cd /home/pi/robot_bipedo/pi
python3 pi_gui_gpio.py
```
En la terminal de la Pi, **sin timeout**, tienen que aparecer:
```
[PI-CLIENT] Cámara IP lista (celular).
[PI-CLIENT] Conectado a tcp://<IP_PC>:5555
[UART] Conectado a /dev/ttyAMA0 @ 115200 baud
```
Y las ruedas empiezan a rodar (modo atracción).

---

## Probar el flujo completo

1. **Mano** a la cámara → ruedas giran al revés 1s y frenan → pantalla de QR.
2. **QR** → oferta → cédula (botones) → factura → registro.
3. **"Muchas gracias"** → ruedas ruedan de nuevo.
4. Si pasan **20s sin QR** → gracias + vuelve a rodar (no se traba).
5. La venta aparece en el dashboard (`localhost:8000`) y en Firebase.

## Cómo parar / apagar

- **Cerrar el programa:** `Ctrl+C` o `ESC` en la Pi → las ruedas **frenan solas**.
- **Freno de emergencia manual** (por si el programa se cerró mal):
  ```
  python3 -c "from pi_uart import UARTController; u=UARTController(); u.connect(); u.send_command('PARAR'); u.disconnect()"
  ```
- **Freno físico instantáneo:** cortar la batería de la VESC.
- **Apagar todo:** no se pierde ninguna configuración. Mañana solo repetís esta guía.

---

## Problemas comunes

| Síntoma | Causa | Solución |
|---|---|---|
| Pi: `timeout de red` | `pc_ip` viejo o `pc_server.py` cerrado | Poné la IP nueva de la PC; confirmá que `pc_server.py` corra |
| Cámara no conecta | `camara_url` con IP vieja del celular | Actualizá la IP del celular en el config |
| `localhost:8000` no abre | `api_server.py` no corre | Abrí la 2ª ventana y corré `py api_server.py` |
| Solo gira una rueda | CAN a la 2ª VESC | Revisá que la VESC esclava esté encendida y con CAN |
| Ruedas siguen tras cerrar | Se cerró mal el programa | Usá el freno manual de arriba |
| Error de JSON al arrancar | Coma mal en el config | `python3 -c "import json; json.load(open('/home/pi/robot_bipedo/config.json'))"` marca la línea |

## Averiguar IPs (resumen)

| Dispositivo | Comando | Dónde |
|---|---|---|
| PC | `ipconfig` → IPv4 | cmd de Windows |
| Raspberry | `hostname -I` | terminal de la Pi |
| Celular | (la muestra IP Webcam) | pantalla de la app |
