"""
=============================================================================
ROBOT PINGÜINO — Control manual desde el celular (override de emergencia)
=============================================================================
Servidor web chiquito y AUTOCONTENIDO (solo librería estándar de Python).
El celular, conectado al hotspot, abre http://<IP-de-la-Pi>:8080 y maneja el
robot con botones. Manda los MISMOS caracteres que el WASD (w/a/s/d/x/+/-) al
ESP32 por el serial USB.

NO toca ningún otro archivo. Usa el mismo puerto USB que recorrido.py, así que
corré UNO u OTRO (no los dos a la vez, comparten el /dev/ttyUSB0).

Uso (en la Pi):
    source ~/mi_proyecto_env/bin/activate
    python3 ~/robot_bipedo/pi/control_manual.py --puerto /dev/ttyUSB0
    # mostrá la IP de la Pi con:  hostname -I
    # en el celular (hotspot):    http://<IP-de-la-Pi>:8080

Mantené apretado un botón para mover; soltalo o tocá PARAR para frenar.
(Si se corta el WiFi, el firmware frena solo a los ~600 ms por el pulso.)
=============================================================================
"""

import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BAUD = 115200
ser = None
_lock = threading.Lock()

PAGINA = """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Control manual — Robot</title>
<style>
 *{box-sizing:border-box;-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent}
 body{margin:0;background:#0f1720;color:#eef3f6;font-family:system-ui,-apple-system,sans-serif;
      height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px}
 h1{font-size:17px;color:#0bb05e;margin:0;letter-spacing:1px}
 #base{width:74vw;max-width:330px;aspect-ratio:1;border-radius:50%;background:#1a2632;
       border:2px solid #2a3a48;position:relative;touch-action:none;
       display:flex;align-items:center;justify-content:center}
 #knob{width:36%;height:36%;border-radius:50%;background:#0bb05e;position:absolute;will-change:transform}
 #eti{font-size:15px;color:#9fb0bd;height:18px}
 .fila{display:flex;gap:14px;align-items:center}
 .fila button{width:26vw;max-width:110px;height:54px;border:none;border-radius:14px;
              background:#233040;color:#eef3f6;font-size:20px}
 .fila button:active{background:#2f4256}
 #stop{background:#c0392b;font-weight:700}
</style></head><body>
<h1>CONTROL MANUAL</h1>
<div id="base"><div id="knob"></div></div>
<div id="eti">quieto</div>
<div class="fila">
  <button onclick="uno('-')">&minus; vel</button>
  <button id="stop">PARAR</button>
  <button onclick="uno('+')">+ vel</button>
</div>
<script>
 function uno(c){ fetch('/cmd?c='+encodeURIComponent(c)).catch(function(){}); }
 var base=document.getElementById('base'), knob=document.getElementById('knob'), eti=document.getElementById('eti');
 var cmd='x', timer=null, activo=false;
 var NOMBRE={w:'adelante',s:'atras',a:'giro izq',d:'giro der',q:'curva izq',e:'curva der',x:'quieto'};

 function sector(dx,dy,dist,R){
   if(dist < R*0.30) return 'x';
   var ang=Math.atan2(-dy,dx)*180/Math.PI;   // 0=der, 90=arriba, -90=abajo
   if(ang>=67.5 && ang<112.5) return 'w';     // arriba = adelante
   if(ang>=22.5 && ang<67.5)  return 'e';     // arriba-der = curva der
   if(ang>=-22.5&& ang<22.5)  return 'd';     // der = giro der
   if(ang>=112.5&&ang<157.5)  return 'q';     // arriba-izq = curva izq
   if(ang>=157.5||ang<-157.5) return 'a';     // izq = giro izq
   return 's';                                 // abajo y diagonales de abajo = atras
 }
 function mover(e){
   var r=base.getBoundingClientRect();
   var cx=r.left+r.width/2, cy=r.top+r.height/2;
   var dx=e.clientX-cx, dy=e.clientY-cy;
   var R=r.width*0.32;
   var dist=Math.hypot(dx,dy);
   if(dist>R){ dx*=R/dist; dy*=R/dist; dist=R; }
   knob.style.transform='translate('+dx+'px,'+dy+'px)';
   cmd=sector(dx,dy,dist,R);
   eti.textContent=NOMBRE[cmd];
 }
 function empezar(e){ activo=true; mover(e); uno(cmd); clearInterval(timer); timer=setInterval(function(){uno(cmd);},200); }
 function fin(){ activo=false; cmd='x'; uno('x'); clearInterval(timer); timer=null;
                knob.style.transform='translate(0,0)'; eti.textContent='quieto'; }
 base.addEventListener('pointerdown',function(e){e.preventDefault(); try{base.setPointerCapture(e.pointerId);}catch(x){} empezar(e);});
 base.addEventListener('pointermove',function(e){ if(activo){e.preventDefault(); mover(e);} });
 // Soltar EN CUALQUIER LADO frena (por si arrastraste el dedo fuera del joystick)
 window.addEventListener('pointerup',function(e){ if(activo) fin(); });
 window.addEventListener('pointercancel',function(e){ if(activo) fin(); });
 // Botón PARAR: freno de emergencia, siempre disponible (aunque no estés tocando el joystick)
 var stopBtn=document.getElementById('stop');
 function frenar_ya(e){ if(e){e.preventDefault(); e.stopPropagation();} fin(); uno('x'); }
 stopBtn.addEventListener('pointerdown',frenar_ya);
 stopBtn.addEventListener('click',frenar_ya);
 window.addEventListener('contextmenu',function(e){e.preventDefault();});
</script></body></html>"""


def escribir(ch):
    if ser is None:
        return
    try:
        with _lock:
            ser.write(ch.encode("ascii"))
    except Exception as e:
        print("[CONTROL] error escribiendo al serial:", e)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # silenciar el log de cada request

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/cmd":
            c = (parse_qs(u.query).get("c", [""])[0] or "")[:1]
            if c:
                escribir(c)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGINA.encode("utf-8"))


def main():
    global ser
    ap = argparse.ArgumentParser(description="Control manual del robot desde el celular.")
    ap.add_argument("--puerto", default="/dev/ttyUSB0", help="puerto serie del ESP32")
    ap.add_argument("--web", type=int, default=8080, help="puerto del servidor web")
    args = ap.parse_args()

    try:
        import serial
        ser = serial.Serial(args.puerto, BAUD, timeout=0.2)
        time.sleep(0.3)
        print(f"[CONTROL] Serial abierto en {args.puerto}")
    except Exception as e:
        print(f"[CONTROL] OJO: no pude abrir {args.puerto} ({e}).")
        print("[CONTROL] La página igual carga, pero los botones no moverán nada.")
        ser = None

    srv = ThreadingHTTPServer(("0.0.0.0", args.web), Handler)
    print(f"[CONTROL] Servidor listo. En el celular (hotspot): http://<IP-de-la-Pi>:{args.web}")
    print("[CONTROL] Mirá la IP de la Pi con: hostname -I")
    print("[CONTROL] Ctrl+C para salir (frena el robot).")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        escribir("x")
        if ser:
            ser.close()
        print("\n[CONTROL] Cerrado. Robot frenado.")


if __name__ == "__main__":
    main()
