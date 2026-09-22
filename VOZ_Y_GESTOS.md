# Voz, gestos y ojos — guía rápida

El robot no tiene parlante: **el celular hace de parlante**. Además de hablar,
muestra la cara del robot y las **instrucciones del paso** en el que estás.

```
 Pantalla del robot (ojos)          Celular del visitante
 ┌──────────────────────┐           ┌────────────────────┐
 │        ◕   ◕         │  WiFi     │  🐧 Pingüi         │
 │  "Bienvenido a ESPOL"│ ────────► │  «Bienvenido a     │
 │                      │           │    ESPOL»  🔊      │
 └──────────────────────┘           │  1. Escaneá el QR  │
   interaccion_ojos.py              │  2. Tocá el enlace │
   + voz_server.py  :8090           └────────────────────┘
```

---

## 1. Arrancar

En la Pi, igual que siempre — la voz se levanta sola con los ojos:

```
source ~/mi_proyecto_env/bin/activate
ROBOT_PC_IP=IP_DE_LA_PC DISPLAY=:0 python3 ~/robot_bipedo/pi/interaccion_ojos.py
```

En el arranque imprime la dirección de la voz:

```
[OJOS] Panel 480x320 · lienzo 480x320 · rotación 0°
[VOZ] Servidor listo. En el celular: http://<IP-de-la-Pi>:8090
[OJOS] Voz del robot en el celular: http://10.172.223.131:8090
```

Para apagar la voz (solo ojos, como antes): `ROBOT_VOZ=0` delante del comando.
Para cambiar el puerto: `ROBOT_VOZ_PUERTO=9000` o `interaccion.voz_puerto` en
`config.json`.

## 2. Conectar el celular

1. El celular al **mismo hotspot** que la Pi.
2. Abrir `http://<IP-de-la-Pi>:8090`.
   Atajo: al arrancar, **la pantalla del robot muestra ese QR durante 12 s**
   (y cuando quieras, con la tecla **V**).
3. Tocar **🔊 Activar voz** una vez y subir el volumen.

> El toque en "Activar voz" es obligatorio: iOS y Android no dejan que una
> página hable sin que el usuario toque algo antes. Si no lo tocás, la página
> igual muestra el texto y las instrucciones, pero muda.

Pueden conectarse **varios celulares a la vez**: todos escuchan lo mismo.

## 3. Qué hace solo

| Situación | Qué pasa |
|---|---|
| Nadie cerca | Duerme y cada 25 s invita: *"¡Bienvenido a ESPOL!"* |
| Llega alguien | Saluda y arranca la charla: PhyCom → actividades → unite al club (≈1 min) |
| La persona se va | Corta la charla a los 6 s y vuelve a dormir |
| 👍 **Pulgar arriba** | Muestra el QR del formulario + instrucciones paso a paso en el celu |
| ✋ **Palma abierta** | Busca un QR 15 s y canta la promoción |

Teclas en la Pi: **1** QR de datos · **2** leer QR · **C** charla completa ·
**V** QR de la voz · **ESC/Q** salir.

## 4. Cambiar lo que dice

Todo el guion está en **`pi/dialogos.py`**, separado del código. Arriba de todo
está el bloque que casi siempre alcanza con tocar:

```python
CLUB = {
    "nombre":      "PhyCom",
    "universidad": "ESPOL",
    "que_es":      "el club de física y computación",
    "reunion":     "los miércoles por la tarde",
    "lugar":       "el laboratorio de física",
    "contacto":    "escaneá el código y te contamos todo",
}
```

> ⚠️ **Revisá esos datos antes del evento.** Las actividades y el día de
> reunión son un borrador: cambialos por los reales.

Para ver el guion completo con tiempos:

```
python3 pi/dialogos.py
```

Cada frase lleva emoción, gesto y sonido:

```python
_linea("¡Bienvenido a ESPOL!", Ojos.FELIZ, "saltar", "hola")
#       texto                  ojos         gesto     sonido en el celular
```

- **Emociones**: `NEUTRO FELIZ ENOJADO SORPRENDIDO TRISTE ESTRELLADO DORMIDO AMOR HABLANDO`
- **Gestos**: `guino asentir negar saltar alrededor`
- **Sonidos**: `hola chispa ok tada pop` (se sintetizan en el celular, no hay archivos)

## 5. Los ojos ahora llenan la pantalla

Antes los ojos tenían un tamaño fijo y una escala adivinada (`LH/450`): en el
Tontec salían chicos y corridos a una esquina. Ahora `ojos.ajustar_a(ancho, alto)`
los dimensiona solos para llenar el panel, sea 480×320, 800×480 o el HDMI.

Ocupan el **88 % del ancho** en cualquier pantalla. Si querés afinarlo, en
`pi/ojos.py`:

```python
PCT_ANCHO = 0.88   # cuánto del ancho ocupan los dos ojos + el hueco
PCT_ALTO  = 0.60   # alto del ojo respecto del alto disponible
HUECO     = 0.42   # separación, como fracción del ancho de un ojo
```

Probalos sin el robot (se redimensiona con la ventana):

```
python3 pi/ojos.py          # 1-9 emociones · G guiño · A sí · N no · S salto
```

## 6. Probar la voz sin el robot

```
python3 pi/voz_server.py --demo
```

Levanta el servidor y recita la charla completa en bucle. Abrí
`http://<IP>:8090` en el celular. También podés forzar un guion a mano:

```
http://<IP>:8090/decir?guion=phycom
http://<IP>:8090/decir?texto=Hola%20mundo
```

Guiones disponibles: `atraccion saludo phycom actividades invitacion
instr_qr instr_form instr_control despedida`.

## 7. Si algo no anda

| Síntoma | Causa casi segura |
|---|---|
| La página abre pero no habla | Falta tocar **Activar voz**, o está en silencio / volumen bajo |
| "Sin conexión con el robot" | El celular no está en el hotspot, o la IP cambió |
| No abre la página | Puerto 8090 ocupado → `ROBOT_VOZ_PUERTO=9000` |
| Habla en inglés | El celular no tiene voz en español instalada (Ajustes → Idioma → Voz) |
| Los ojos siguen chicos | Quedó una copia vieja de `ojos.py` en la Pi: volvé a copiar `pi/` |
