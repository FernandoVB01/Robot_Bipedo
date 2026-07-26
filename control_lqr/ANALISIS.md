# Análisis Completo del Sistema — Robot Bípedo Auto-balanceado

**Fecha:** 2026-07-18 · **Herramientas:** MATLAB/Simulink R2021a
**Parámetros:** `Mb=1.2 kg, Mw=0.3 kg, Ib=0.015 kg·m², Iw=4e-4 kg·m², R=0.05 m, x_com=0.02 m, y_com=0.15 m`
**Scripts:** `matlab/fase1_modelado.m`, `matlab/fase2_lqr.m`, `matlab/fase2_analisis.m`

---

## 1. Geometría del centro de masa

| Magnitud | Valor | Interpretación |
|---|---|---|
| `L_eff` | 0.1513 m | Longitud efectiva del péndulo equivalente |
| `γ` | 0.1326 rad = **7.59°** | Desfase estático por la rodilla: el robot debe **reclinarse 7.59°** para que el COM quede sobre el eje de ruedas |

El equilibrio dinámico es `θ_eq = −γ`; toda la síntesis se hace en la coordenada
desviación `δθ = θ + γ`.

## 2. Estabilidad en lazo abierto

Polos: `{0, 0, +8.875, −8.875}` rad/s.

- Los **dos polos en el origen** son la posición del carro: sin control, el robot
  deriva libremente (doble integrador).
- El **polo en +8.875 rad/s** (1.41 Hz) confirma la inestabilidad del péndulo
  invertido: constante de tiempo de caída ≈ **113 ms**. Es la escala de tiempo que
  el controlador debe vencer, y fija el requisito mínimo de muestreo/actuación.
- El polo espejo en −8.875 rad/s es el modo péndulo estable (estructura hamiltoniana
  típica: los polos vienen en pares ±).

**Conclusión:** el sistema es inestable en lazo abierto (como debe ser) y el control
debe cerrar el lazo con un ancho de banda claramente superior a ~9 rad/s.

## 3. Controlabilidad

```
rango(ctrb(A,B)) = 4 de 4        → completamente controlable
valores singulares = {1.12e4, 1.12e4, 12, 12}
número de condición = 933
```

- Con **un solo actuador** (torque de ruedas) se pueden gobernar los 4 estados:
  condición necesaria y suficiente para que exista el LQR.
- El número de condición (933) indica anisotropía moderada: los modos de ángulo son
  "baratos" de mover y los de posición "caros" (coherente con la física: inclinar el
  robot es inmediato, trasladarlo requiere integrar esa inclinación). No es un
  problema numérico, pero explica por qué la posición se regula mucho más lento que
  el ángulo.

## 4. Observabilidad (análisis por sensor)

| Configuración de sensores | rango(obsv) | Veredicto |
|---|---|---|
| Solo IMU (mide `δθ`) | **2** de 4 | Insuficiente: `x, ẋ` invisibles |
| Solo encoders (miden `x`) | 4 de 4 | Suficiente en teoría (vía la dinámica acoplada), pero débil/ruidoso en la práctica |
| IMU + encoders | **4** de 4 | Configuración correcta |

**Justificación física:** la posición no aparece en la dinámica (la columna de `x`
en `A` es nula), así que desde la IMU jamás se puede reconstruir dónde está el robot.
Esto **justifica la arquitectura del nodo ROS**: IMU para `δθ, δθ̇` + encoders para
`x, ẋ`. Realimentar solo la IMU (tentación común) deja dos estados sin control y el
robot "se balancea pero se va caminando".

## 5. Lazo cerrado — dominio del tiempo

Ganancias: `K_cont = [−1.414, −2.538, −13.512, −1.602]`,
`Kd (dlqr, 100 Hz) = [−0.682, −1.228, −6.859, −0.817]`.
(Signos negativos correctos: por la fase no mínima, ante caída hacia adelante el
robot acelera hacia adelante para meterse bajo el COM.)

Polos de `A − BK` (continuo):

| Polo | ζ | ωn [rad/s] | τ | Rol físico |
|---|---|---|---|---|
| −143.7 | 1.0 | 143.7 | 7 ms | Modo rápido de actuación (torque/ángulo-tasa) |
| −10.9 | 1.0 | 10.9 | 92 ms | Recuperación del ángulo (≈ espejo del polo inestable) |
| −0.73 ± 0.57i | **0.79** | 0.93 | 1.37 s | Regulación de posición |

Discreto a 100 Hz: `|z| = {0.263, 0.897, 0.993, 0.993}` — todos dentro del círculo
unitario ✅, con los mismos ζ y ωn equivalentes (la discretización preserva la
dinámica diseñada).

**Jerarquía de tiempos correcta para un balancín:** equilibrar es ~15× más rápido
que trasladarse. El par complejo con ζ = 0.79 da una respuesta de posición casi
críticamente amortiguada (un solo sobrepaso pequeño).

**Verificación por simulación** (`respuesta_perturbacion.png`): condición inicial de
2.9° corregida en ~0.4 s; escalón de 0.3 N·m en t=1 s produce pico de −2.4° y
torque máximo de 0.34 N·m (17% del límite de 2 N·m del motor).

**Offset estacionario ante perturbación sostenida:** sin acción integral,
`x_ss = d/K₁`. Con el lazo discreto implementado: `x_ss = 0.3/0.682 = −0.44 m`
(confirmado por la simulación: −0.43 m en t=5 s, aún convergiendo). El ángulo sí
regresa exactamente a `δθ = 0`. Si el requisito exige rechazo total en posición,
se necesita LQR aumentado con integrador de `x`.

## 6. Respuesta en frecuencia y robustez

Función de lazo `L(s) = K(sI−A)⁻¹B` (ver `analisis_bode_lazo.png`):

| Métrica | Lazo continuo | Lazo discreto (100 Hz) | Criterio |
|---|---|---|---|
| Margen de ganancia | −25.5 dB *(inferior)* / ∞ *(superior)* | **7.82 dB** (superior, en ωN = 314 rad/s) | > 6 dB ✅ |
| Margen de fase | **85.6°** (cruce 156 rad/s) | **58.6°** (cruce 83.5 rad/s) | > 45° ✅ |
| Pico de sensibilidad Ms | **1.000** (0 dB) | **1.685** (4.5 dB) | < 2 ✅ |

Lecturas importantes:

1. **El margen de ganancia continuo "−25.5 dB" no es malo: es un margen inferior.**
   Como la planta es inestable en lazo abierto, el lazo necesita ganancia mínima
   para estabilizar: puede *caer* hasta el 5.3% del valor nominal (motor débil,
   batería baja) antes de perder estabilidad, y puede *crecer* sin límite. Es la
   garantía clásica del LQR: GM ∈ [1/2, ∞) — aquí incluso mejor.
2. **Ms = 1.000 confirma la desigualdad de Kalman** `|1 + L(jω)| ≥ 1 ∀ω`: el LQR
   continuo nunca amplifica perturbaciones en ninguna frecuencia. Obtener
   exactamente 1.000 numéricamente es la validación más fuerte de que el diseño
   está bien planteado.
3. **La discretización cuesta robustez** (resultado conocido: las garantías LQR no
   sobreviven al muestreo): PM cae de 85.6° a 58.6° y Ms sube a 1.69. Aun así,
   ambos quedan dentro de los criterios de ingeniería (PM > 45°, Ms < 2).
4. **Ancho de banda de control** (cruce del lazo discreto): 83.5 rad/s ≈ **13 Hz**,
   unas 9× la frecuencia del polo inestable (1.41 Hz) — separación sana.

## 7. Justificación de la frecuencia de muestreo (100 Hz)

- Respecto al polo inestable (8.88 rad/s): `ωs/ω_inest = 628/8.88 ≈ 71×` — sobrado.
- Respecto al modo más rápido del lazo cerrado continuo (143.7 rad/s):
  `ωs/ω_max = 4.4×` — **ajustado** (regla práctica: ≥ 10×). Consecuencias visibles:
  - `dlqr` re-sintoniza la ganancia (Kd ≈ K/2): el diseño discreto "afloja" el modo
    rápido que el muestreo no puede sostener. Por eso el offset de posición de la
    implementación (−0.44 m) difiere del continuo (−0.21 m).
  - Es la causa principal de la pérdida de margen de fase del punto 6.3.
- **Opciones si se quiere recuperar robustez:** (a) subir `Rlqr` para que el modo
  rápido continuo baje de ~144 a ~60 rad/s, o (b) muestrear a 200–500 Hz si el
  hardware lo permite. Con los márgenes actuales (PM 58.6°, Ms 1.69) **no es
  obligatorio**: el diseño es válido tal cual.

## 8. Conclusiones

1. Modelo Euler-Lagrange con desfase `γ` derivado simbólicamente y verificado contra
   la forma analítica cerrada; equilibrio en `θ = −γ = −7.59°`.
2. Sistema inestable en lazo abierto (polo +8.88 rad/s), completamente controlable
   con un actuador, y observable **solo si** se combinan IMU + encoders.
3. El LQR discreto a 100 Hz estabiliza con jerarquía temporal correcta (ángulo en
   ~0.1 s, posición en ~1.4 s, ζ = 0.79) usando ≤ 17% del torque disponible ante
   perturbaciones de 0.3 N·m.
4. Robustez verificada en frecuencia: PM 58.6°, GM 7.8 dB, Ms 1.69 — dentro de
   criterios estándar; el LQR continuo exhibe sus garantías teóricas exactas
   (PM ≥ 60°, Ms ≤ 1).
5. Limitaciones conocidas y cuantificadas: offset de −0.44 m ante perturbación
   sostenida (falta acción integral) y relación muestreo/dinámica rápida de 4.4×
   (mitigada por el rediseño con `dlqr`).

## Archivos de evidencia

| Archivo | Contenido |
|---|---|
| `matlab/respuesta_perturbacion.png` | Respuesta temporal x, δθ, τ ante escalón |
| `matlab/analisis_polos.png` | Mapa de polos: lazo abierto vs cerrado |
| `matlab/analisis_bode_lazo.png` | Bode del lazo con márgenes (continuo vs discreto) |
| `matlab/lqr_design.mat` | A, B, C, D, Ad, Bd, K, Kd, Ts, γ |
| `matlab/fase2_lazo_cerrado.slx` | Modelo Simulink del lazo cerrado |
