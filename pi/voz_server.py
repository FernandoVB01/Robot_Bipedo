#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — La VOZ del robot sale por el celular
=============================================================================
El robot no tiene parlante: el celular hace de parlante. Esta es la pieza que
lo conecta.

    Pantalla del robot (interaccion_ojos.py)
              │  publica: frase + gesto + sonido + instrucciones
              ▼
    VozServer  ── HTTP :8090 ──►  celular (navegador)
                                   · lee la frase en voz alta
                                   · hace sonar el efecto
                                   · muestra las instrucciones del paso

Todo con la librería ESTÁNDAR de Python y sin una sola dependencia en el
celular: la voz es la del propio navegador (Web Speech API) y los efectos se
sintetizan con WebAudio. No hay archivos .mp3 que copiar ni CDN que cargar —
el hotspot del evento NO tiene internet y cualquier <script src="https://…">
dejaría la página muda justo en la demo.

Uso desde el programa de los ojos:

    from voz_server import VozServer
    voz = VozServer(puerto=8090)
    voz.start()
    voz.publicar(linea, fase="phycom")     # línea del guion (dialogos.py)
    voz.instrucciones(["Escaneá el QR", "Tocá el enlace"])

Uso suelto, para probar sin el robot:

    python3 voz_server.py            # abre http://<IP-de-la-Pi>:8090
    python3 voz_server.py --demo     # además recita la charla completa

En el celular, conectado al mismo hotspot:  http://<IP-de-la-Pi>:8090
La primera vez hay que tocar "Activar voz": iOS y Android no dejan que una
página hable sin que el usuario toque algo antes.
=============================================================================
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))

PUERTO_DEFECTO = 8090


# ═══════════════════════════════════════════════════════════════════════════
# PÁGINA DEL CELULAR (autocontenida: sin CDN, sin archivos externos)
# ═══════════════════════════════════════════════════════════════════════════
PAGINA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#06090f">
<title>Pingüi — voz del robot</title>
<style>
 :root{--bg:#06090f;--card:#121826;--line:#25304a;--fg:#eaf2ff;--mut:#8ea0bd;
       --cian:#40e0ff;--oro:#ffd84d;--verde:#39d98a;--rojo:#ff5d5d;--rosa:#ff6080}
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 html,body{height:100%}
 body{margin:0;background:radial-gradient(circle at 50% 0%,#101a2e,var(--bg) 70%);
      color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
      display:flex;flex-direction:column;overflow:hidden;
      padding:max(10px,env(safe-area-inset-top)) 14px max(12px,env(safe-area-inset-bottom));
      -webkit-user-select:none;user-select:none}

 header{display:flex;align-items:center;justify-content:space-between;gap:8px}
 h1{font-size:17px;margin:0;font-weight:700}
 .st{display:flex;align-items:center;gap:6px;color:var(--mut);font-size:12px}
 .dot{width:9px;height:9px;border-radius:50%;background:var(--rojo)}
 .dot.on{background:var(--verde)}

 /* ── Cara: dos ojos que copian lo que hace el robot ── */
 .cara{display:flex;justify-content:center;align-items:center;gap:30px;
       height:150px;margin:8px 0 4px}
 .ojo{width:92px;height:92px;border-radius:28px;background:var(--cian);
      display:grid;place-items:center;font-size:86px;line-height:1;
      transition:height .12s,border-radius .18s,background .3s,transform .18s}
 .cara.feliz .ojo{height:44px;border-radius:0 0 48px 48px}
 .cara.dormido .ojo{height:12px;border-radius:8px;background:#2c3d5a}
 .cara.sorprendido .ojo{width:104px;height:110px;border-radius:34px}
 .cara.enojado .ojo:first-child{transform:skewY(11deg)}
 .cara.enojado .ojo:last-child{transform:skewY(-11deg)}
 .cara.triste .ojo:first-child{transform:skewY(-11deg)}
 .cara.triste .ojo:last-child{transform:skewY(11deg)}
 .cara.estrellado .ojo{background:none;border-radius:0}
 .cara.estrellado .ojo::after{content:"⭐"}
 .cara.amor .ojo{background:none;border-radius:0}
 .cara.amor .ojo::after{content:"❤️"}
 .cara.hablando .ojo{animation:lat .9s ease-in-out infinite}
 @keyframes lat{50%{height:74px}}
 @media (max-height:640px){.cara{height:112px}.ojo{width:70px;height:70px;border-radius:22px;font-size:66px}}
 /* gestos */
 .cara.g-saltar{animation:salta .7s ease}
 @keyframes salta{50%{transform:translateY(-22px)}}
 .cara.g-asentir{animation:asiente .9s ease}
 @keyframes asiente{25%{transform:translateY(12px)}75%{transform:translateY(-6px)}}
 .cara.g-negar{animation:niega .9s ease}
 @keyframes niega{25%{transform:translateX(-16px)}75%{transform:translateX(16px)}}
 .cara.g-guino .ojo:last-child{height:8px;border-radius:6px}
 .cara.g-alrededor{animation:mira 2s ease}
 @keyframes mira{25%{transform:translateX(-20px)}75%{transform:translateX(20px)}}

 /* ── Lo que dice ── */
 .burbuja{background:var(--card);border:1px solid var(--line);border-radius:18px;
          padding:16px;min-height:112px;display:flex;align-items:center;
          justify-content:center;text-align:center}
 .burbuja p{margin:0;font-size:22px;line-height:1.35;font-weight:600}
 .fase{color:var(--mut);font-size:11px;text-transform:uppercase;
       letter-spacing:1px;text-align:center;margin:10px 0 4px}

 /* ── Instrucciones del paso ── */
 .pasos{flex:1;overflow-y:auto;margin-top:10px}
 .paso{display:flex;gap:10px;align-items:flex-start;background:var(--card);
       border-radius:12px;padding:11px 13px;margin-bottom:8px;font-size:14px}
 .paso .n{width:22px;height:22px;flex:none;border-radius:50%;background:var(--cian);
          color:#06131c;font-weight:800;font-size:12px;display:grid;place-items:center}
 .vacio{color:var(--mut);font-size:13px;text-align:center;padding:14px}

 /* ── Botones ── */
 .barra{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:8px}
 button{font:inherit;font-weight:700;border:0;border-radius:13px;padding:13px;
        background:var(--card);color:var(--fg);font-size:14px;border:1px solid var(--line)}
 button:active{filter:brightness(1.35)}
 #activar{grid-column:1/3;background:var(--cian);color:#04131b;font-size:17px;padding:16px;border:0}
 button.mudo{background:#3a1c1c;color:#ffb4b4}
 .chip{display:inline-block;padding:2px 8px;border-radius:7px;background:#1b2437;
       color:var(--mut);font-size:11px;margin-left:6px}
</style>
</head>
<body>

<header>
  <h1>🐧 Pingüi <span class="chip" id="chip-voz">voz apagada</span></h1>
  <div class="st"><span id="dot" class="dot"></span><span id="stxt">Conectando…</span></div>
</header>

<div class="cara" id="cara"><div class="ojo"></div><div class="ojo"></div></div>

<div class="fase" id="fase">esperando al robot</div>
<div class="burbuja"><p id="texto">…</p></div>

<div class="pasos" id="pasos">
  <div class="vacio">Las instrucciones aparecen acá cuando el robot te guíe.</div>
</div>

<div class="barra">
  <button id="activar">🔊 Activar voz</button>
  <button id="repetir">↻ Repetir</button>
  <button id="silencio" class="">🔇 Silenciar</button>
</div>

<script>
"use strict";
const $ = (id) => document.getElementById(id);

let vozLista = false;     /* el usuario ya tocó "Activar voz" */
let silenciado = false;
let ultimaSeq = -1;
let ultimoTexto = "";
let fallos = 0;
let ctx = null;           /* AudioContext, se crea al activar */

/* ── Voz del navegador ──────────────────────────────────────────────────── */
let vozES = null;
function elegirVoz(){
  const vs = window.speechSynthesis ? speechSynthesis.getVoices() : [];
  /* Preferimos una voz de español latino; si no hay, cualquier español. */
  vozES = vs.find(v => /es[-_]?(419|MX|US|AR|CO|EC|CL|PE)/i.test(v.lang))
       || vs.find(v => /^es/i.test(v.lang))
       || null;
}
if (window.speechSynthesis){
  elegirVoz();
  speechSynthesis.onvoiceschanged = elegirVoz;
}

function hablar(texto){
  if (!vozLista || silenciado || !texto || !window.speechSynthesis) return;
  speechSynthesis.cancel();                 /* no encimar frases */
  const u = new SpeechSynthesisUtterance(texto);
  if (vozES) u.voice = vozES;
  u.lang  = vozES ? vozES.lang : "es-ES";
  u.rate  = 1.0;
  u.pitch = 1.15;                           /* un poco agudo: es un pingüino */
  speechSynthesis.speak(u);
}

/* ── Efectos de sonido (sintetizados, sin archivos) ─────────────────────── */
const EFECTOS = {
  hola:   [[523,0,.10],[659,.10,.10],[784,.20,.16]],
  chispa: [[880,0,.06],[1175,.07,.06]],
  ok:     [[784,0,.08],[1047,.09,.12]],
  tada:   [[523,0,.10],[659,.10,.10],[784,.20,.10],[1047,.30,.26]],
  pop:    [[440,0,.05],[660,.05,.05]],
};
function sonar(nombre){
  if (!ctx || silenciado) return;
  const notas = EFECTOS[nombre];
  if (!notas) return;
  const t0 = ctx.currentTime;
  for (const [hz, off, dur] of notas){
    const osc = ctx.createOscillator(), g = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.value = hz;
    g.gain.setValueAtTime(0.0001, t0 + off);
    g.gain.exponentialRampToValueAtTime(0.28, t0 + off + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + off + dur);
    osc.connect(g).connect(ctx.destination);
    osc.start(t0 + off);
    osc.stop(t0 + off + dur + 0.02);
  }
}

/* ── Cara: copia la emoción y el gesto del robot ────────────────────────── */
const ANIMOS = ["neutro","feliz","enojado","sorprendido","triste",
                "estrellado","dormido","amor","hablando"];
const GESTOS = ["saltar","asentir","negar","guino","alrededor"];
function pintarCara(animo, gesto){
  const c = $("cara");
  ANIMOS.forEach(a => c.classList.remove(a));
  GESTOS.forEach(g => c.classList.remove("g-" + g));
  if (animo) c.classList.add(animo);
  if (gesto){
    /* Reiniciar la animación: quitar, forzar reflow y volver a poner. */
    void c.offsetWidth;
    c.classList.add("g-" + gesto);
    setTimeout(() => c.classList.remove("g-" + gesto), 2100);
  }
}

/* ── Instrucciones del paso ─────────────────────────────────────────────── */
function pintarPasos(lista){
  const cont = $("pasos");
  if (!lista || !lista.length){
    cont.innerHTML = '<div class="vacio">Las instrucciones aparecen acá cuando el robot te guíe.</div>';
    return;
  }
  cont.innerHTML = lista.map((t, i) =>
    '<div class="paso"><div class="n">' + (i+1) + '</div><div>' + escapar(t) + '</div></div>'
  ).join("");
}
function escapar(s){
  return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

/* ── Sondeo del estado del robot ────────────────────────────────────────── */
async function sondear(){
  try{
    const r = await fetch("/estado", {cache:"no-store"});
    const d = await r.json();
    marcarRed(true);

    if (d.seq !== ultimaSeq){
      ultimaSeq  = d.seq;
      ultimoTexto = d.texto || "";
      $("texto").textContent = ultimoTexto || "…";
      $("fase").textContent  = d.fase || "";
      pintarCara(d.animo, d.gesto);
      pintarPasos(d.instrucciones);
      if (d.sonido) sonar(d.sonido);
      if (ultimoTexto) hablar(ultimoTexto);
    }
  }catch(e){
    marcarRed(false);
  }
}
function marcarRed(ok){
  fallos = ok ? 0 : fallos + 1;
  const malo = fallos >= 3;
  $("dot").classList.toggle("on", !malo);
  $("stxt").textContent = malo ? "Sin conexión con el robot" : "Conectado";
}
setInterval(sondear, 400);
sondear();

/* ── Botones ────────────────────────────────────────────────────────────── */
$("activar").addEventListener("click", () => {
  /* Este toque es el que desbloquea audio y voz en iOS/Android. */
  try{ ctx = ctx || new (window.AudioContext || window.webkitAudioContext)(); }catch(e){}
  if (ctx && ctx.state === "suspended") ctx.resume();
  vozLista = true;
  silenciado = false;
  elegirVoz();
  /* Frase corta de prueba: confirma que hay voz y "calienta" el motor TTS. */
  hablar("Voz activada");
  sonar("ok");
  $("chip-voz").textContent = "voz activa";
  $("activar").textContent = "🔊 Voz activada";
  $("activar").style.background = "var(--verde)";
});

$("repetir").addEventListener("click", () => {
  if (ultimoTexto) hablar(ultimoTexto);
});

$("silencio").addEventListener("click", () => {
  silenciado = !silenciado;
  if (silenciado && window.speechSynthesis) speechSynthesis.cancel();
  $("silencio").textContent = silenciado ? "🔊 Reanudar" : "🔇 Silenciar";
  $("silencio").classList.toggle("mudo", silenciado);
  $("chip-voz").textContent = silenciado ? "silenciado"
                            : (vozLista ? "voz activa" : "voz apagada");
});

/* Al volver de segundo plano, iOS deja el TTS trabado: se limpia la cola. */
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && window.speechSynthesis) speechSynthesis.cancel();
});
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════
# SERVIDOR
# ═══════════════════════════════════════════════════════════════════════════

class VozServer:
    """
    Publica lo que el robot está diciendo para que el celular lo reproduzca.

    El estado vive en memoria y se sirve como JSON en /estado. El celular lo
    sondea cada 400 ms y, cuando cambia el número de secuencia, habla la frase
    nueva. Sondeo simple en vez de WebSocket a propósito: son cuatro líneas,
    no se cae cuando el WiFi parpadea y funciona en cualquier navegador viejo.
    """

    def __init__(self, puerto: int = PUERTO_DEFECTO):
        self.puerto = puerto
        self._lock  = threading.Lock()
        self._srv   = None
        self._hilo  = None
        self._estado = {
            "seq": 0,
            "texto": "",
            "animo": "dormido",
            "gesto": None,
            "sonido": None,
            "fase": "esperando",
            "instrucciones": [],
            "ts": time.time(),
        }

    # ── API que usa la pantalla del robot ─────────────────────────────────

    def publicar(self, linea: dict, fase: str = None, instrucciones: list = None):
        """Publica una línea del guion (dict de dialogos.py)."""
        with self._lock:
            self._estado["seq"]    += 1
            self._estado["texto"]   = linea.get("texto", "")
            self._estado["animo"]   = linea.get("animo", "hablando")
            self._estado["gesto"]   = linea.get("gesto")
            self._estado["sonido"]  = linea.get("sonido")
            self._estado["ts"]      = time.time()
            if fase is not None:
                self._estado["fase"] = fase
            if instrucciones is not None:
                self._estado["instrucciones"] = list(instrucciones)

    def decir(self, texto: str, animo: str = "hablando",
              gesto: str = None, sonido: str = None, fase: str = None):
        """Atajo para publicar una frase suelta sin armar el dict del guion."""
        self.publicar({"texto": texto, "animo": animo,
                       "gesto": gesto, "sonido": sonido}, fase=fase)

    def instrucciones(self, lista: list, fase: str = None):
        """Cambia SOLO la lista de pasos (sin decir nada nuevo)."""
        with self._lock:
            self._estado["seq"] += 1
            self._estado["instrucciones"] = list(lista or [])
            self._estado["texto"]  = ""
            self._estado["gesto"]  = None
            self._estado["sonido"] = None
            if fase is not None:
                self._estado["fase"] = fase

    def fase(self, nombre: str):
        with self._lock:
            self._estado["fase"] = nombre

    def animo(self, animo: str):
        """Sincroniza la cara del celular con la del robot, sin hablar."""
        with self._lock:
            if self._estado["animo"] != animo:
                self._estado["seq"]  += 1
                self._estado["animo"] = animo
                self._estado["texto"] = self._estado["texto"]
                self._estado["gesto"] = None
                self._estado["sonido"] = None

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._estado)

    # ── Ciclo de vida ─────────────────────────────────────────────────────

    def start(self):
        servidor = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass                       # silenciar el log de cada request

            def _responder(self, cuerpo: bytes, tipo: str):
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(cuerpo)
                except (BrokenPipeError, ConnectionResetError):
                    pass                   # el celular cerró: no es un error

            def do_GET(self):
                u = urlparse(self.path)

                if u.path == "/estado":
                    self._responder(
                        json.dumps(servidor.snapshot()).encode("utf-8"),
                        "application/json; charset=utf-8")

                elif u.path == "/decir":
                    # Prueba manual: /decir?texto=hola  o  /decir?guion=phycom
                    q = parse_qs(u.query)
                    if q.get("guion"):
                        import dialogos
                        threading.Thread(
                            target=servidor.recitar,
                            args=(dialogos.guion(q["guion"][0]),),
                            kwargs={"fase": q["guion"][0]},
                            daemon=True).start()
                    elif q.get("texto"):
                        servidor.decir(q["texto"][0])
                    self._responder(b"ok", "text/plain")

                else:
                    self._responder(PAGINA.encode("utf-8"),
                                    "text/html; charset=utf-8")

        self._srv = ThreadingHTTPServer(("0.0.0.0", self.puerto), Handler)
        self._srv.daemon_threads = True
        self._hilo = threading.Thread(target=self._srv.serve_forever,
                                      daemon=True, name="VozServer")
        self._hilo.start()
        print(f"[VOZ] Servidor listo. En el celular: http://<IP-de-la-Pi>:{self.puerto}")
        print("[VOZ] (mirá la IP con 'hostname -I'; hay que tocar 'Activar voz' una vez)")
        return self

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    # ── Recitar un guion completo (bloqueante; usalo en un hilo) ──────────

    def recitar(self, lineas: list, fase: str = None, entre: float = 0.25):
        """Va publicando las líneas del guion una tras otra, respetando `dur`."""
        for l in lineas:
            self.publicar(l, fase=fase)
            time.sleep(l.get("dur", 3.0) + entre)


# ─────────────────────────────────────────────────────────────────────────────
# Uso suelto:  python3 voz_server.py [--demo] [--puerto 8090]
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Voz del robot en el celular.")
    ap.add_argument("--puerto", type=int, default=PUERTO_DEFECTO)
    ap.add_argument("--demo", action="store_true",
                    help="recita la charla completa en bucle")
    args = ap.parse_args()

    voz = VozServer(args.puerto).start()

    try:
        if args.demo:
            import dialogos
            voz.instrucciones([
                "Conectá el celular al mismo WiFi que el robot.",
                "Tocá «Activar voz» una vez.",
                "Subí el volumen del teléfono.",
            ], fase="demo")
            time.sleep(6)
            while True:
                voz.recitar(dialogos.CHARLA_COMPLETA, fase="charla")
                time.sleep(3)
        else:
            print("[VOZ] Probá:  /decir?texto=hola   ·   /decir?guion=phycom")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        voz.stop()
        print("\n[VOZ] Cerrado.")
