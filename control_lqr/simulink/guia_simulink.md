# Guía Simulink — Lazo cerrado con LQR discreto (Fase 2)

Crea un modelo nuevo (`fase2_lazo_cerrado.slx`) y ejecuta antes
`matlab/fase2_lqr.m` para tener `A, B, C, D, Kd, Ts` en el workspace.

## Bloques, paso a paso

1. **Planta continua** — bloque *State-Space* (librería Continuous):
   `A, B, C, D`; en *Initial conditions* pon `[0; 0; 0.05; 0]`
   (≈2.9° de error inicial en δθ). Representa al robot "real".

2. **Muestreo del controlador** — bloque *Zero-Order Hold* (Discrete) a la
   salida de la planta, *Sample time* = `Ts`. Emula que el microcontrolador
   lee sensores a 100 Hz.

3. **Ganancia LQR** — bloque *Gain* tras el ZOH: valor `Kd`, y en sus
   parámetros cambia *Multiplication* a **Matrix(K*u)** (imprescindible:
   entra el vector de 4 estados, sale un escalar).

4. **Realimentación negativa** — bloque *Sum* con signos `|+-`:
   entrada `+` = perturbación, entrada `-` = salida del Gain.
   Su salida es `u = d - Kd·x` y va a la entrada de la planta (lazo cerrado).

5. **Perturbación escalón** — bloque *Step* a la entrada `+` del Sum:
   *Step time* = 1 s, *Final value* = 0.3 (empujón sostenido de 0.3 N·m en
   el eje). Prueba también un pulso restándole un segundo Step retardado 0.2 s.

6. **Visualización** —
   - *Demux* (o *Selector*) a la salida de la planta para separar los 4
     estados; conecta δθ (señal 3) a un *Scope*, con un *Gain* de `180/pi`
     si lo quieres en grados.
   - Segundo *Scope* a la salida del Sum para ver el **torque del motor**.
   - Opcional: bloques *To Workspace* (`theta_out`, `tau_out`, formato
     *Structure with time*) para graficar en MATLAB.

7. **Configuración de simulación** — Solver `ode45` (variable-step),
   *Stop time* = 5 s.

## Qué debes observar

Al llegar el escalón, el torque salta inmediatamente en sentido contrario,
δθ se desvía unos pocos grados transitoriamente y ambos convergen.

- Si `x` deriva demasiado → sube `Qlqr(1,1)`.
- Si el torque pico excede lo que da tu motor → sube `Rlqr`.
