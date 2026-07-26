#pragma once
/* Estimador de angulo, ley de control y maquina de seguridad. */
#include <stdbool.h>
#include "mpu6050.h"

typedef enum {
    EST_DESARMADO = 0,   /* sin torque; es el estado de arranque */
    EST_ARMADO,          /* lazo cerrado */
    EST_FALLA            /* se paso de inclinacion: torque cero y enganchado */
} estado_t;

typedef struct {
    float pitch;         /* [rad] inclinacion medida (filtro complementario) */
    float pitch_acc;     /* [rad] solo acelerometro (diagnostico) */
    float pitch_rate;    /* [rad/s] */
    float yaw_rate;      /* [rad/s] giro sobre el eje vertical */
    float dtheta;        /* [rad] pitch - equilibrio: lo que el control lleva a 0 */
    float x, dx;         /* [m], [m/s] odometria por ERPM */
    float u_nm;          /* [N.m] torque TOTAL comandado */
    float i_izq, i_der;  /* [A] por rueda */
    estado_t estado;
} bal_estado_t;

void bal_init(void);

/** Un paso del lazo: actualiza el angulo, la odometria y calcula el torque.
 *  NO habla con la VESC: devuelve las corrientes en el estado. */
void bal_paso(const mpu_muestra_t *m, float erpm, float dt);

const bal_estado_t *bal_get(void);
const char *bal_estado_str(void);

/** Arma. Falla (devuelve false) si el robot no esta cerca del equilibrio:
 *  armar tumbado provocaria un latigazo de torque. */
bool bal_armar(void);
void bal_desarmar(void);

/** Fija el equilibrio en la inclinacion actual (comando EQ). */
void bal_fijar_equilibrio(void);
float bal_equilibrio(void);

void bal_set_ganancias(float kth, float kdth, float kx, float kdx);
void bal_get_ganancias(float *dst4);

/** Referencias de navegacion (m/s y rad/s). Se rampean solas. */
void bal_set_referencia(float v, float w);

/** Corriente fija de prueba para la Etapa 1 (camino de corriente).
 *  Solo tiene efecto DESARMADO. */
void bal_test_corriente(float amp);
