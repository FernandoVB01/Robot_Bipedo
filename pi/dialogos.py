#!/home/pi/mi_proyecto_env/bin/python3
"""
=============================================================================
ROBOT PINGÜINO — GUION: lo que dice y cómo lo actúa
=============================================================================
Acá vive TODO el texto que el robot "habla". El código de la pantalla
(interaccion_ojos.py) no tiene ni una frase escrita adentro: lee de acá.
Así se corrige el guion sin tocar la lógica, que es lo que uno quiere el día
del evento.

Cada línea del guion es un diccionario:

    {
      "texto":  lo que se dice y se muestra,
      "animo":  emoción de los ojos mientras lo dice (Ojos.*),
      "gesto":  gesto corto que la acompaña ("guino"/"asentir"/"negar"/
                "saltar"/"alrededor"/None),
      "sonido": efecto que suena en el celular ("hola"/"chispa"/"ok"/
                "tada"/"pop"/None),
      "dur":    segundos que dura en pantalla (si no se pone, se calcula
                por el largo del texto),
    }

El celular lee la frase en voz alta (voz del navegador, sin internet) y hace
sonar el efecto. La pantalla del robot muestra los ojos y el texto grande.

─────────────────────────────────────────────────────────────────────────────
⚠️  REVISÁ ESTO ANTES DEL EVENTO
─────────────────────────────────────────────────────────────────────────────
Las frases sobre PhyCom (actividades, días de reunión, contacto) son un
borrador de ejemplo. Cambialas por los datos reales del club: están todas
juntas en PHYCOM y ACTIVIDADES, y en el diccionario CLUB de acá abajo.
=============================================================================
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ojos import Ojos                       # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# DATOS DEL CLUB — editá esto y el guion se actualiza solo
# ═══════════════════════════════════════════════════════════════════════════
CLUB = {
    "nombre":      "PhyCom",
    "universidad": "ESPOL",
    "que_es":      "el club de física y computación",
    "reunion":     "los miércoles por la tarde",
    "lugar":       "el laboratorio de física",
    "contacto":    "escaneá el código y te contamos todo",
}


def _seg(texto: str) -> float:
    """
    Cuánto dura una frase en pantalla si no se especifica.

    Regla simple: ~13 caracteres por segundo (velocidad cómoda de lectura en
    voz alta), con un piso de 2 s para que ninguna frase pase de largo.
    """
    return max(2.0, min(9.0, len(texto) / 13.0 + 1.0))


def _linea(texto, animo=Ojos.HABLANDO, gesto=None, sonido=None, dur=None):
    return {"texto": texto, "animo": animo, "gesto": gesto,
            "sonido": sonido, "dur": dur if dur else _seg(texto)}


# ═══════════════════════════════════════════════════════════════════════════
# GUIONES
# ═══════════════════════════════════════════════════════════════════════════

# ── Atracción: lo que dice solo, cuando no hay nadie cerca ──────────────────
ATRACCION = [
    _linea(f"¡Bienvenido a {CLUB['universidad']}!",
           Ojos.FELIZ, "saltar", "hola"),
    _linea("Acercate, que te quiero mostrar algo.",
           Ojos.NEUTRO, "alrededor", None),
]

# ── Saludo: alguien se paró enfrente ────────────────────────────────────────
SALUDO = [
    _linea("¡Hola! Soy Pingüi, el robot de PhyCom.",
           Ojos.ESTRELLADO, "saltar", "hola"),
    _linea(f"Bienvenido a {CLUB['universidad']}.",
           Ojos.FELIZ, "asentir", None),
]

# ── Quiénes somos ───────────────────────────────────────────────────────────
PHYCOM = [
    _linea(f"{CLUB['nombre']} es {CLUB['que_es']} de {CLUB['universidad']}.",
           Ojos.HABLANDO, None, "chispa"),
    _linea("Somos estudiantes que construimos cosas que funcionan de verdad.",
           Ojos.HABLANDO, "asentir", None),
    _linea("Yo soy una de ellas: me diseñaron, me programaron y me armaron acá.",
           Ojos.ESTRELLADO, "saltar", "tada"),
]

# ── Qué hacemos ─────────────────────────────────────────────────────────────
ACTIVIDADES = [
    _linea("¿Querés saber qué hacemos?",
           Ojos.SORPRENDIDO, "guino", "pop"),
    _linea("Robótica: robots que caminan, ruedan y ven, como yo.",
           Ojos.HABLANDO, None, None),
    _linea("Electrónica y programación: desde el circuito hasta la aplicación.",
           Ojos.HABLANDO, None, None),
    _linea("Y proyectos de física aplicada para competencias y ferias.",
           Ojos.HABLANDO, "asentir", None),
    _linea(f"Nos reunimos {CLUB['reunion']} en {CLUB['lugar']}.",
           Ojos.FELIZ, None, "chispa"),
]

# ── Invitación a unirse ─────────────────────────────────────────────────────
INVITACION = [
    _linea("No hace falta saber de todo: se aprende haciendo.",
           Ojos.HABLANDO, "negar", None),
    _linea(f"Si te gustó lo que viste, unite a {CLUB['nombre']}.",
           Ojos.AMOR, "saltar", "tada"),
    _linea(CLUB["contacto"],
           Ojos.FELIZ, "asentir", "ok"),
]

# ── Instrucciones del proceso del celular ───────────────────────────────────
# Se dicen mientras la pantalla muestra el QR y el cliente usa el teléfono.
INSTRUCCIONES_QR = [
    _linea("Abrí la cámara de tu celular y apuntá al código de mi pantalla.",
           Ojos.HABLANDO, None, "pop"),
    _linea("Tocá el enlace que te aparece. Se abre solo, sin instalar nada.",
           Ojos.HABLANDO, "asentir", None),
]

INSTRUCCIONES_FORM = [
    _linea("Completá tus datos en el formulario. Son treinta segundos.",
           Ojos.HABLANDO, None, None),
    _linea("Con eso te llega la información del club.",
           Ojos.FELIZ, "guino", "ok"),
]

INSTRUCCIONES_CONTROL = [
    _linea("En tu celular tenés un joystick: con él me manejás.",
           Ojos.SORPRENDIDO, None, "pop"),
    _linea("Arriba avanzo, abajo retrocedo, a los costados giro.",
           Ojos.HABLANDO, "alrededor", None),
    _linea("Soltá el dedo y freno solo. El botón rojo es el freno de emergencia.",
           Ojos.HABLANDO, "asentir", None),
]

# ── Cierre ──────────────────────────────────────────────────────────────────
DESPEDIDA = [
    _linea("¡Gracias por acercarte!",
           Ojos.FELIZ, "saltar", "tada"),
    _linea(f"Nos vemos en {CLUB['nombre']}.",
           Ojos.AMOR, "guino", None),
]


# Todos los guiones, por nombre. El servidor del celular y la pantalla los
# piden así: GUIONES["phycom"].
GUIONES = {
    "atraccion":    ATRACCION,
    "saludo":       SALUDO,
    "phycom":       PHYCOM,
    "actividades":  ACTIVIDADES,
    "invitacion":   INVITACION,
    "instr_qr":     INSTRUCCIONES_QR,
    "instr_form":   INSTRUCCIONES_FORM,
    "instr_control": INSTRUCCIONES_CONTROL,
    "despedida":    DESPEDIDA,
}

# La charla completa, en orden, para cuando alguien se queda escuchando.
CHARLA_COMPLETA = (SALUDO + PHYCOM + ACTIVIDADES + INVITACION + DESPEDIDA)


def guion(nombre: str) -> list:
    """Devuelve un guion por nombre; lista vacía si no existe."""
    return GUIONES.get(nombre, [])


def duracion(lineas: list) -> float:
    """Cuánto dura un guion completo, en segundos."""
    return sum(l["dur"] for l in lineas)


if __name__ == "__main__":
    # python3 dialogos.py  → imprime el guion completo con sus tiempos
    # (la consola de Windows viene en cp1252 y se atraganta con los acentos)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print(f"CLUB: {CLUB['nombre']} - {CLUB['universidad']}\n")
    for nombre, lineas in GUIONES.items():
        print(f"── {nombre.upper()}  ({duracion(lineas):.1f} s)")
        for l in lineas:
            marcas = []
            if l["gesto"]:  marcas.append(f"gesto:{l['gesto']}")
            if l["sonido"]: marcas.append(f"son:{l['sonido']}")
            extra = ("  [" + " ".join(marcas) + "]") if marcas else ""
            print(f"   {l['dur']:4.1f}s  {l['animo']:<12} {l['texto']}{extra}")
        print()
    print(f"CHARLA COMPLETA: {duracion(CHARLA_COMPLETA):.1f} s "
          f"({len(CHARLA_COMPLETA)} líneas)")
