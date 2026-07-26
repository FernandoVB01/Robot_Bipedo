#pragma once
/* =============================================================================
 * CONFIGURACION UNICA DEL ROBOT BALANCIN
 * -----------------------------------------------------------------------------
 * Todo lo que se toca al sintonizar vive aqui. Las ganancias tambien se pueden
 * cambiar en caliente con el comando "K" (ver link.c), sin reflashear.
 * ========================================================================== */

/* ---------------------------------------------------------------- Frecuencias */
#define LOOP_HZ             200          /* lazo de control [Hz] */
#define LOOP_DT             (1.0f / (float)LOOP_HZ)
#define TELEMETRY_HZ        50           /* publicacion a la Pi [Hz] */
#define VESC_POLL_HZ        50           /* consulta de ERPM a la VESC [Hz] */

/* --------------------------------------------------------------- I2C / MPU6050 */
#define PIN_SDA             21
#define PIN_SCL             22
#define I2C_FREQ_HZ         400000
#define MPU_ADDR            0x68

/* ------------------------------------------------------- UART hacia la Pi (UART1) */
#define PI_UART_NUM         UART_NUM_1
#define PI_UART_TX_PIN      17           /* GPIO17 -> Pi RX (GPIO15, pin 10) */
#define PI_UART_RX_PIN      16           /* GPIO16 <- Pi TX (GPIO14, pin 8)  */
#define PI_UART_BAUD        115200

/* ------------------------------------------------------ UART hacia la VESC (UART2) */
#define VESC_UART_NUM       UART_NUM_2
#define VESC_UART_TX_PIN    4            /* GPIO4 -> VESC RX */
#define VESC_UART_RX_PIN    5            /* GPIO5 <- VESC TX */
#define VESC_UART_BAUD      115200
#define VESC_CAN_ID_SLAVE   25           /* 2a VESC por CAN (maestra = 42) */

/* =============================================================================
 * CONVENCION DE EJES  --  VERIFICAR EN LA ETAPA A CON telemetry_viewer.py
 * -----------------------------------------------------------------------------
 * Convencion del modelo: pitch > 0 = inclinado hacia ADELANTE.
 * Con la IMU montada con x hacia adelante y z hacia arriba:
 *      pitch_acc = atan2(-ax, az)
 * Si al inclinar hacia adelante el angulo sale NEGATIVO, pon PITCH_SIGN en -1.
 * Lo mismo con el giroscopio: debe ser positivo al caer hacia adelante.
 * ========================================================================== */
#define PITCH_SIGN          (+1.0f)
#define GYRO_SIGN           (+1.0f)

/* ---------------------------------------------------- Filtro complementario */
#define COMP_TAU_S          0.50f        /* mas alto = confia mas en el giro */

/* -------------------------------------------------------- Geometria y motor */
#define WHEEL_RADIUS_M      0.084f       /* rueda de 168 mm de diametro */
#define MOTOR_POLE_PAIRS    15           /* VERIFICAR en VESC Tool */
#define KT_NM_PER_A         0.60f        /* VERIFICAR: el datasheet es
                                          * inconsistente (la fila Torque_Max
                                          * no cuadra con las otras dos).
                                          * Medir: aplicar corriente conocida
                                          * con la rueda frenada por un brazo
                                          * de palanca y pesar la fuerza. */

/* ------------------------------------------------------- Punto de equilibrio */
/* gamma del modelo (fase1_modelado.m). Recalibrable en caliente con "EQ". */
#define GAMMA_RAD           0.132552f    /* 7.59 grados */

/* =============================================================================
 * GANANCIAS  --  u = kth*dth + kdth*dth_dot + kx*(x-x_ref) + kdx*(dx-v_ref)
 * -----------------------------------------------------------------------------
 * Son los valores de la K de MATLAB cambiados de signo (la K de lqr() sale
 * negativa porque la ley alli es u = -K*x).
 *
 * ARRANCAR EN MODO PID: kx = kdx = 0  ->  esto ES el PID de angulo del
 * roadmap del equipo. Activar kx/kdx solo cuando la VESC ya entregue ERPM
 * (si no, se realimenta ruido).
 *
 * REFERENCIA del modelo a 200 Hz CON PARAMETROS DE EJEMPLO (NO son los del
 * robot real):  kth = 9.43, kdth = 1.12, kx = 0.96, kdx = 1.73
 * Los valores por defecto de abajo son DELIBERADAMENTE BAJOS: se sube kth
 * de a poco (Etapa 4) hasta que se sostenga firme sin temblar.
 * ========================================================================== */
#define K_THETA_DEF         2.0f         /* [N.m/rad]   subir de a poco */
#define K_DTHETA_DEF        0.20f        /* [N.m/(rad/s)] */
#define K_X_DEF             0.0f         /* [N.m/m]     0 = modo PID */
#define K_DX_DEF            0.0f         /* [N.m/(m/s)] 0 = modo PID */

/* ------------------------------------------------------------------ Limites */
#define TAU_MAX_NM          4.0f         /* techo de torque TOTAL */
#define CURRENT_MAX_A       10.0f        /* techo por rueda [A] */
#define TAU_YAW_MAX_NM      0.5f         /* el giro nunca roba torque al balance */
#define TILT_FAULT_RAD      0.52f        /* 30 grados -> corta y pasa a FALLA */
#define TILT_ARM_RAD        0.17f        /* 10 grados: ventana para poder armar */
#define ERR_X_MAX_M         0.50f        /* saturacion del error de posicion */
#define ERR_DX_MAX_MS       1.00f        /* saturacion del error de velocidad */
#define ACC_V_MS2           0.30f        /* rampa de velocidad de referencia */
#define ACC_W_RADS2         1.50f        /* rampa de giro de referencia */

/* ------------------------------------------------- Captura en rafaga (CAPTURA) */
#define CAPTURA_MUESTRAS    600          /* 600 / 200 Hz = 3 s */
