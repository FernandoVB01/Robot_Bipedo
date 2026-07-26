/* =============================================================================
 * ESTIMADOR + LEY DE CONTROL + SEGURIDAD
 * -----------------------------------------------------------------------------
 * La ley es realimentacion de estado:
 *
 *   u = kth*dtheta + kdth*dtheta_dot + kx*(x - x_ref) + kdx*(dx - v_ref)
 *
 * Con kx = kdx = 0 esto ES el PID de angulo (Kp/Kd) del roadmap del equipo.
 * Activando kx y kdx queda el LQR completo de 4 estados, que ademas regula
 * la posicion. Es el mismo codigo: solo cambian las ganancias.
 *
 * Por que hace falta kx/kdx: con realimentacion de angulo solamente, el
 * sistema es observable solo en rango 2 de 4 (ver ANALISIS.md, seccion 4).
 * El robot se equilibra pero se va caminando.
 * ========================================================================== */
#include "balance.h"
#include "config.h"

#include <math.h>

static bal_estado_t s;
static float s_equilibrio = GAMMA_RAD;      /* pitch objetivo [rad] */
static float s_k[4];                        /* kth, kdth, kx, kdx */
static float s_v_cmd, s_w_cmd;              /* consignas crudas */
static float s_v_ref, s_w_ref;              /* consignas rampeadas */
static float s_x_ref;
static float s_test_amp;                    /* corriente de prueba, desarmado */
static bool  s_filtro_listo;

static float saturar(float v, float lim)
{
    if (v >  lim) return  lim;
    if (v < -lim) return -lim;
    return v;
}

void bal_init(void)
{
    s_k[0] = K_THETA_DEF;
    s_k[1] = K_DTHETA_DEF;
    s_k[2] = K_X_DEF;
    s_k[3] = K_DX_DEF;
    s.estado = EST_DESARMADO;
    s_filtro_listo = false;
}

/* ------------------------------------------------------------------ estimador */
static void estimar_angulo(const mpu_muestra_t *m, float dt)
{
    /* Angulo por gravedad. El montaje se confirma en la Etapa A y se ajusta
     * con PITCH_SIGN en config.h. */
    float pitch_acc = PITCH_SIGN * atan2f(-m->ax, m->az);
    float giro      = GYRO_SIGN * m->gy;

    s.pitch_acc  = pitch_acc;
    s.pitch_rate = giro;
    s.yaw_rate   = m->gz;

    if (!s_filtro_listo || dt <= 0.0f || dt > 0.1f) {
        s.pitch = pitch_acc;               /* arranque o hueco: confia en el acel */
        s_filtro_listo = true;
        return;
    }
    /* Filtro complementario: el giro manda en el corto plazo, el acelerometro
     * corrige la deriva en el largo plazo. */
    const float a = COMP_TAU_S / (COMP_TAU_S + dt);
    s.pitch = a * (s.pitch + giro * dt) + (1.0f - a) * pitch_acc;
}

/* ----------------------------------------------------------------- odometria */
static void estimar_odometria(float erpm, float dt)
{
    /* ERPM -> rad/s mecanicos -> m/s en el suelo */
    const float w_rueda = (erpm / (float)MOTOR_POLE_PAIRS) * (2.0f * (float)M_PI / 60.0f);
    s.dx = w_rueda * WHEEL_RADIUS_M;
    s.x += s.dx * dt;
}

/* ---------------------------------------------------------------- ley y limites */
void bal_paso(const mpu_muestra_t *m, float erpm, float dt)
{
    estimar_angulo(m, dt);
    estimar_odometria(erpm, dt);

    s.dtheta = s.pitch - s_equilibrio;

    /* --- Seguridad: fuera de rango no hay LQR lineal que valga ------------ */
    if (fabsf(s.dtheta) > TILT_FAULT_RAD && s.estado == EST_ARMADO) {
        s.estado = EST_FALLA;
    }

    if (s.estado != EST_ARMADO) {
        /* Desarmado o en falla: sin lazo. Se permite la corriente de prueba
         * de la Etapa 1, que es deliberadamente independiente del balanceo. */
        s.u_nm = 0.0f;
        s.i_izq = s.i_der = (s.estado == EST_DESARMADO) ? s_test_amp : 0.0f;
        s_x_ref = s.x;                     /* referencia pegada al estado real */
        s_v_ref = s_w_ref = 0.0f;
        return;
    }

    /* --- Rampas: un escalon de velocidad es un latigazo para un pendulo --- */
    s_v_ref += saturar(s_v_cmd - s_v_ref, ACC_V_MS2 * dt);
    s_w_ref += saturar(s_w_cmd - s_w_ref, ACC_W_RADS2 * dt);
    s_x_ref += s_v_ref * dt;

    /* --- Realimentacion de estado (PID de angulo si kx = kdx = 0) --------- */
    const float e_x  = saturar(s.x - s_x_ref, ERR_X_MAX_M);
    const float e_dx = saturar(s.dx - s_v_ref, ERR_DX_MAX_MS);

    float u = s_k[0] * s.dtheta
            + s_k[1] * s.pitch_rate
            + s_k[2] * e_x
            + s_k[3] * e_dx;
    u = saturar(u, TAU_MAX_NM);
    s.u_nm = u;

    /* --- Giro: torque diferencial, acotado para que nunca robe el balance -- */
    /* Control por velocidad de guiñada (sin integrar el rumbo: no deriva). */
    const float tau_yaw = saturar(0.15f * (s_w_ref - s.yaw_rate), TAU_YAW_MAX_NM);

    /* --- Reparto y conversion a corriente -------------------------------- */
    const float tau_izq = u * 0.5f - tau_yaw;
    const float tau_der = u * 0.5f + tau_yaw;
    s.i_izq = saturar(tau_izq / KT_NM_PER_A, CURRENT_MAX_A);
    s.i_der = saturar(tau_der / KT_NM_PER_A, CURRENT_MAX_A);
}

const bal_estado_t *bal_get(void) { return &s; }

const char *bal_estado_str(void)
{
    switch (s.estado) {
        case EST_ARMADO: return "ARMED";
        case EST_FALLA:  return "FAULT";
        default:         return "DISARMED";
    }
}

bool bal_armar(void)
{
    if (fabsf(s.pitch - s_equilibrio) > TILT_ARM_RAD) return false;
    s_x_ref = s.x;
    s_v_ref = s_w_ref = 0.0f;
    s_v_cmd = s_w_cmd = 0.0f;
    s_test_amp = 0.0f;
    s.estado = EST_ARMADO;
    return true;
}

void bal_desarmar(void)
{
    s.estado = EST_DESARMADO;
    s_test_amp = 0.0f;
    s_v_cmd = s_w_cmd = 0.0f;
}

void bal_fijar_equilibrio(void)  { s_equilibrio = s.pitch; }
float bal_equilibrio(void)       { return s_equilibrio; }

void bal_set_ganancias(float kth, float kdth, float kx, float kdx)
{
    s_k[0] = kth; s_k[1] = kdth; s_k[2] = kx; s_k[3] = kdx;
}

void bal_get_ganancias(float *dst4)
{
    for (int i = 0; i < 4; ++i) dst4[i] = s_k[i];
}

void bal_set_referencia(float v, float w) { s_v_cmd = v; s_w_cmd = w; }

void bal_test_corriente(float amp)
{
    if (s.estado == EST_DESARMADO) s_test_amp = saturar(amp, CURRENT_MAX_A);
}
