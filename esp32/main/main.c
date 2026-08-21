/**
 * =============================================================================
 * ROBOT PINGÜINO — Firmware ESP32 (ESP-IDF, C puro, sin Arduino)
 * =============================================================================
 * Función   : Control de movimiento del robot diferencial. Recibe órdenes de la
 *             Raspberry Pi por UART, las traduce a duty de cada rueda y las
 *             manda al controlador VESC dual. Lee el tacómetro de la VESC para
 *             cerrar el lazo de los giros.
 *
 * Reparto de núcleos (lo que pidió el equipo):
 *   Core 1 (APP_CPU) → task_pi_link   : habla con la Raspberry Pi
 *   Core 0 (PRO_CPU) → task_motion    : traduce a las llantas (VESC TX)
 *                      task_vesc_rx   : lee la respuesta de la VESC (odometría)
 *
 * Hardware:
 *   UART0 (GPIO 1/3)  → Consola de depuración (monitor serie)
 *   UART1 (GPIO 16/17)→ Raspberry Pi (comandos de control)
 *   UART2 (GPIO 4/5)  → VESC maestra (protocolo binario oficial)
 *   VESC maestra ID 42 ──CAN──► VESC esclava ID 25
 *
 * Build: ESP-IDF v5.x  ·  idf.py build  ·  idf.py -p COM7 flash monitor
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * TRES COSAS QUE CAMBIARON RESPECTO DE LA VERSIÓN ANTERIOR — leer antes de tocar
 * ─────────────────────────────────────────────────────────────────────────────
 *  1. TRACCIÓN DIFERENCIAL DE VERDAD. Antes `vesc_set_duty()` mandaba el MISMO
 *     duty a las dos ruedas, así que el robot solo podía ir derecho o de
 *     reversa. Ahora `vesc_set_duty_lr(izq, der)` maneja cada rueda por
 *     separado: gira sobre su propio eje, pivotea sobre una rueda y avanza en
 *     arco.
 *
 *  2. NADA BLOQUEA. Antes AVANZAR_T y GIRAR_180 corrían un `while` que dejaba
 *     la tarea de control sorda hasta terminar: un PARAR mandado a mitad de un
 *     avance de 5 s se quedaba esperando en la cola. Con alguien manejando
 *     desde el celular eso es peligroso. Ahora todo es una máquina de estados
 *     que avanza en ticks de 20 ms y CUALQUIER comando nuevo interrumpe al
 *     anterior en el acto.
 *
 *  3. SE FRENA, NO SE SUELTA. Antes "parar" era duty = 0, o sea rueda libre: el
 *     robot seguía de largo por inercia. Ese es el motivo de que un giro de 90°
 *     terminara en 106-110° — un ~18% constante, demasiado parejo para ser
 *     ruido. Ahora se frena con COMM_SET_CURRENT_BRAKE.
 * =============================================================================
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "nvs.h"

/* ─────────────────────────────────────────────────────────────────────────────
 * CONFIGURACIÓN DE PINES Y UART
 * ──────────────────────────────────────────────────────────────────────────── */

/* UART1: comunicación con la Raspberry Pi */
#define PI_UART_NUM         UART_NUM_1
#define PI_UART_TX_PIN      17          /* GPIO 17 → Pi RX (GPIO 15, pin 10) */
#define PI_UART_RX_PIN      16          /* GPIO 16 ← Pi TX (GPIO 14, pin 8)  */
#define PI_UART_BAUD        115200
#define PI_UART_BUF_SIZE    256

/* UART2: comunicación con el VESC */
#define VESC_UART_NUM       UART_NUM_2
#define VESC_UART_TX_PIN    4           /* GPIO 4 → VESC RX  */
#define VESC_UART_RX_PIN    5           /* GPIO 5 ← VESC TX  */
#define VESC_UART_BAUD      115200
#define VESC_UART_BUF_SIZE  512         /* Más grande: ahora también LEEMOS */

/* LED de estado (LED azul integrado en muchas placas ESP32 = GPIO 2) */
#define STATUS_LED_PIN      GPIO_NUM_2

/* ── Controlador VESC dual ────────────────────────────────────────────────────
 * El ESP32 habla por UART a la VESC MAESTRA (ID 42) y le manda al segundo motor
 * por CAN: la maestra reenvía el paquete (COMM_FORWARD_CAN).                  */
#define VESC_DUAL              1   /* 1 = dos motores (uno UART, otro CAN) */
#define VESC_CAN_ID_MOTOR_2    25  /* CAN ID de la 2ª VESC (VESC Tool → App → Controller ID) */

/* Signo de cada rueda: convierte "velocidad hacia adelante" en duty físico.
 *
 * En el chasis actual las dos VESC giran igual con el mismo duty (por eso el
 * robot iba derecho con el firmware viejo, que mandaba un solo duty a las dos).
 * Si al mandar MOV:0.3:0 el robot GIRA en lugar de avanzar, es que un motor
 * está montado espejado: poné -1 en el que corresponda.                       */
#define MOTOR_IZQ_SIGNO     (+1.0f)   /* rueda izquierda = VESC maestra (UART) */
#define MOTOR_DER_SIGNO     (+1.0f)   /* rueda derecha   = VESC esclava (CAN)  */

/* IDs de comando del protocolo VESC (de datatypes.h del firmware bldc) */
#define COMM_GET_VALUES         4
#define COMM_SET_DUTY           5
#define COMM_SET_CURRENT        6
#define COMM_SET_CURRENT_BRAKE  7
#define COMM_FORWARD_CAN       34

/* Parámetros de movimiento por defecto (ajustables en caliente con CAL:) */
#define VESC_DUTY_AVANZAR   0.25f   /* 25 % del ciclo de trabajo */
#define VESC_DUTY_ACERCAR   0.12f   /* 12 % — velocidad lenta    */
#define VESC_DUTY_RETRO    -0.20f   /* Negativo = retroceso      */

/* Valores por defecto para AVANZAR_T si la Pi no manda parámetros */
#define AVANZAR_T_MS_DEFAULT    5000    /* 5 segundos */
#define AVANZAR_T_DUTY_DEFAULT  0.25f   /* 25 % */

/* Periodo del lazo de movimiento. Todo el firmware late a este ritmo.
 * No subirlo de 100 ms: la VESC frena el motor por seguridad si no recibe un
 * comando en ~1 s, y necesitamos margen de sobra.                            */
#define TICK_MS                 20

/* DEADMAN (hombre muerto) — el seguro más importante de todo el firmware.
 * En teleoperación la Pi manda MOV: unas 10 veces por segundo. Si se corta el
 * WiFi, se cuelga el celular o alguien cierra la WebApp con el acelerador
 * apretado, dejamos de recibir MOV y el robot se quedaría andando solo.
 * Pasado este tiempo sin un MOV nuevo → freno.                               */
#define TELEOP_TIMEOUT_MS       400

/* Cuánto esperamos a que la VESC conteste el tacómetro antes de decidir que no
 * está cableado el RX y hacer el giro a ciegas (por tiempo).                  */
#define ODO_ESPERA_MS           300
/* Con más de esto sin datos frescos, la odometría se considera muerta. */
#define ODO_FRESCURA_MS         250

/* ── Sensores infrarrojos (obstáculos) — a futuro ─────────────────────────────
 * Poné IR_SENSORS_ENABLED en 1 cuando cablees los sensores.
 * La mayoría de sensores IR de obstáculo (tipo FC-51) dan nivel BAJO (0) al
 * detectar algo cerca; ajustá IR_OBSTACLE_LEVEL según tu sensor.
 * Nota: GPIO 34/35 son solo-entrada en el ESP32 (sin pull interno).           */
#define IR_SENSORS_ENABLED   0
#define IR_SENSOR_LEFT_PIN   GPIO_NUM_34
#define IR_SENSOR_RIGHT_PIN  GPIO_NUM_35
#define IR_OBSTACLE_LEVEL    0

/* ─────────────────────────────────────────────────────────────────────────────
 * PARÁMETROS CALIBRABLES (comando CAL:, persistidos en NVS)
 *
 * Antes calibrar era editar un #define, recompilar y reflashear por cada
 * prueba. Ahora se ajusta por UART y sobrevive al reinicio:
 *
 *     CAL:ms_grado:7.2      ← el que arregla el giro de 90°
 *     CAL:guardar:1         ← lo escribe en NVS
 *     CAL:listar:0          ← imprime todos los valores actuales
 * ──────────────────────────────────────────────────────────────────────────── */

typedef struct {
    float ms_grado;     /* ms de giro por grado, a giro_duty. Base del lazo abierto */
    float giro_duty;    /* duty con el que se ejecutan los giros [0..1] */
    float cpr;          /* cuentas de tacómetro por VUELTA DE RUEDA (ver medir_cpr) */
    float radio_mm;     /* radio de la rueda en mm */
    float via_mm;       /* distancia entre el centro de las dos ruedas, en mm */
    float freno_a;      /* corriente de frenado, en amperios */
    float anticipo;     /* fracción del giro en la que se empieza a frenar [0..0.5] */
} calib_t;

/* Valores de fábrica.
 *
 * ms_grado = 7.2 sale de la medición del equipo: pidiendo 90° el robot daba
 * 106-110°, o sea ~18% de más. Si su tiempo para "90°" era de 780 ms, el tiempo
 * honesto por grado es 780/108 ≈ 7.2 ms, no 780/90 ≈ 8.7. Este número es un
 * PUNTO DE PARTIDA: hay que medirlo (ver "CÓMO CALIBRAR" al final del archivo).
 *
 * radio_mm = 84 corresponde a la rueda hub de 168 mm de diámetro.
 * cpr = 90 asume 15 pares de polos (6 cuentas por revolución eléctrica × 15).
 * Casi seguro hay que corregirlo: se mide en 30 segundos, ver CAL:listar.     */
static calib_t g_cal = {
    .ms_grado  = 7.2f,
    .giro_duty = 0.12f,
    .cpr       = 90.0f,
    .radio_mm  = 84.0f,
    .via_mm    = 300.0f,
    .freno_a   = 4.0f,
    .anticipo  = 0.10f,
};

#define NVS_NAMESPACE  "robot"
#define NVS_KEY_CAL    "calib"

/* ─────────────────────────────────────────────────────────────────────────────
 * TIPOS Y ENUMERACIONES
 * ──────────────────────────────────────────────────────────────────────────── */

typedef enum {
    CMD_AVANZAR = 0,
    CMD_PARAR,
    CMD_GIRAR_180,
    CMD_ACERCAR,
    CMD_RETROCEDER,
    CMD_AVANZAR_T,        /* Avanzar por tiempo y velocidad, luego parar */
    CMD_RODAR,            /* Rodar continuo a una velocidad (modo atracción) */
    CMD_MOV,              /* Teleoperación diferencial: MOV:<v>:<w> */
    CMD_GIRO,             /* Giro sobre el eje por grados: GIRO:<grados> */
    CMD_PIVOTE,           /* Arco pivotando sobre una rueda */
    CMD_RUTINA,           /* Secuencia enlatada (BAILE, SALUDO, GIRO_180) */
    CMD_MODO,             /* AUTO / TELEOP / PAUSA */
    CMD_CAL,              /* Calibración en caliente */
    CMD_PING,
    CMD_DESCONOCIDO
} robot_command_t;

/* Modo de operación global. Lo fija la Pi según en qué punto del flujo esté. */
typedef enum {
    MODO_AUTO = 0,   /* ruta autónoma: el robot hace lo suyo */
    MODO_TELEOP,     /* manda el celular del cliente (con deadman) */
    MODO_PAUSA       /* congelado durante la interacción */
} modo_t;

/* Estado de la máquina de movimiento. */
typedef enum {
    MOV_FRENADO = 0, /* freno activo */
    MOV_LIBRE,       /* duty 0, rueda libre */
    MOV_RODAR,       /* duty continuo en las dos ruedas (modo atracción) */
    MOV_TELEOP,      /* sigue el último MOV: recibido, con deadman */
    MOV_TEMPORIZADO, /* AVANZAR_T: duty fijo durante N ms */
    MOV_GIRO,        /* giro sobre el eje hasta completar los grados */
    MOV_PIVOTE       /* arco: una rueda más lenta que la otra */
} mov_estado_t;

typedef struct {
    robot_command_t cmd;
    char            raw[48];       /* Texto original del comando */
    int             duracion_ms;   /* AVANZAR_T: cuánto avanzar */
    float           duty;          /* AVANZAR_T / RODAR: velocidad [-1..1] */
    float           v, w;          /* MOV: lineal y angular [-1..1] */
    float           grados;        /* GIRO / PIVOTE */
    float           k;             /* PIVOTE: factor de la rueda interna [0..1] */
    int             lado;          /* PIVOTE: -1 izquierda, +1 derecha */
    int             modo;          /* MODO */
    char            arg[24];       /* RUTINA / CAL: nombre del parámetro */
    float           valor;         /* CAL: valor */
} command_msg_t;

/* Un paso de una rutina enlatada. */
typedef enum {
    PASO_FIN = 0,
    PASO_GIRO,       /* p1 = grados */
    PASO_TIEMPO,     /* p1 = ms, p2 = duty */
    PASO_ESPERA      /* p1 = ms quieto */
} paso_tipo_t;

typedef struct {
    paso_tipo_t tipo;
    float       p1;
    float       p2;
} paso_t;

/* ── El baile ────────────────────────────────────────────────────────────────
 * Giros alternados sobre el eje, un pasito adelante y atrás, y una vuelta
 * completa para cerrar. Con el pingüino animándose en la pantalla de la Pi al
 * mismo tiempo.                                                              */
static const paso_t RUTINA_BAILE[] = {
    {PASO_GIRO,    90.0f, 0},
    {PASO_GIRO,   -90.0f, 0},
    {PASO_GIRO,   -90.0f, 0},
    {PASO_GIRO,    90.0f, 0},
    {PASO_TIEMPO, 250.0f,  0.12f},   /* pasito adelante */
    {PASO_TIEMPO, 250.0f, -0.12f},   /* pasito atrás    */
    {PASO_ESPERA, 150.0f, 0},
    {PASO_GIRO,   360.0f, 0},        /* vuelta de gala  */
    {PASO_FIN,     0,     0},
};

/* Saludo corto: un meneo, para cuando detecta a alguien. */
static const paso_t RUTINA_SALUDO[] = {
    {PASO_GIRO,    25.0f, 0},
    {PASO_GIRO,   -50.0f, 0},
    {PASO_GIRO,    25.0f, 0},
    {PASO_FIN,     0,     0},
};

static const paso_t RUTINA_GIRO_180[] = {
    {PASO_GIRO,   180.0f, 0},
    {PASO_FIN,     0,     0},
};

/* ─────────────────────────────────────────────────────────────────────────────
 * VARIABLES GLOBALES
 * ──────────────────────────────────────────────────────────────────────────── */

static const char *TAG_MAIN  = "ROBOT_MAIN";
static const char *TAG_PI    = "UART_PI";
static const char *TAG_VESC  = "UART_VESC";

static QueueHandle_t cmd_queue;          /* Comandos discretos Pi → movimiento */
static SemaphoreHandle_t pi_tx_mutex;    /* Serializa las escrituras al UART de la Pi */

/* Consigna de teleoperación. Se actualiza fuera de la cola a propósito:
 * queremos ÚLTIMO VALOR GANA. Si el WiFi hipa y llegan cinco MOV juntos, el
 * robot debe obedecer el último, no ejecutar los cinco en fila. */
static portMUX_TYPE teleop_mux = portMUX_INITIALIZER_UNLOCKED;
static float    g_teleop_v = 0.0f;
static float    g_teleop_w = 0.0f;
static int64_t  g_teleop_ts_us = 0;

/* Odometría: la escribe task_vesc_rx, la lee task_motion. */
static portMUX_TYPE odo_mux = portMUX_INITIALIZER_UNLOCKED;
static int32_t  g_tach = 0;              /* cuentas del tacómetro (VESC maestra) */
static float    g_vbat = 0.0f;           /* tensión de entrada, para telemetría */
static int64_t  g_odo_ts_us = 0;         /* cuándo llegó el último dato */
static bool     g_odo_alguna_vez = false;

static modo_t   g_modo = MODO_AUTO;

/* ─────────────────────────────────────────────────────────────────────────────
 * UTILIDADES
 * ──────────────────────────────────────────────────────────────────────────── */

static inline int64_t ahora_ms(void) { return esp_timer_get_time() / 1000; }

static inline float clampf(float x, float lo, float hi)
{
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}

/** @brief Escribe una línea al UART de la Pi. Serializado con mutex porque
 *         escriben tanto la tarea de enlace (ACK) como la de movimiento (TEL). */
static void pi_print(const char *s)
{
    if (xSemaphoreTake(pi_tx_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
        uart_write_bytes(PI_UART_NUM, s, strlen(s));
        xSemaphoreGive(pi_tx_mutex);
    }
}

/* ─────────────────────────────────────────────────────────────────────────────
 * NVS — persistencia de la calibración
 * ──────────────────────────────────────────────────────────────────────────── */

static void calib_cargar(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) {
        ESP_LOGI(TAG_MAIN, "NVS sin calibración guardada — usando valores de fábrica.");
        return;
    }
    size_t len = sizeof(calib_t);
    calib_t tmp;
    if (nvs_get_blob(h, NVS_KEY_CAL, &tmp, &len) == ESP_OK && len == sizeof(calib_t)) {
        g_cal = tmp;
        ESP_LOGI(TAG_MAIN, "Calibración cargada de NVS.");
    }
    nvs_close(h);
}

static bool calib_guardar(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return false;
    bool ok = (nvs_set_blob(h, NVS_KEY_CAL, &g_cal, sizeof(calib_t)) == ESP_OK)
              && (nvs_commit(h) == ESP_OK);
    nvs_close(h);
    return ok;
}

static void calib_listar(void)
{
    char buf[192];
    snprintf(buf, sizeof(buf),
             "CAL:ms_grado=%.3f giro_duty=%.3f cpr=%.1f radio_mm=%.1f "
             "via_mm=%.1f freno_a=%.2f anticipo=%.3f\n",
             (double)g_cal.ms_grado, (double)g_cal.giro_duty, (double)g_cal.cpr,
             (double)g_cal.radio_mm, (double)g_cal.via_mm,
             (double)g_cal.freno_a, (double)g_cal.anticipo);
    pi_print(buf);
    ESP_LOGI(TAG_MAIN, "%s", buf);
}

/** @brief Aplica CAL:<param>:<valor>. Devuelve false si el parámetro no existe. */
static bool calib_set(const char *param, float valor)
{
    if      (strcmp(param, "ms_grado")  == 0) g_cal.ms_grado  = valor;
    else if (strcmp(param, "giro_duty") == 0) g_cal.giro_duty = clampf(valor, 0.02f, 1.0f);
    else if (strcmp(param, "cpr")       == 0) g_cal.cpr       = valor;
    else if (strcmp(param, "radio_mm")  == 0) g_cal.radio_mm  = valor;
    else if (strcmp(param, "via_mm")    == 0) g_cal.via_mm    = valor;
    else if (strcmp(param, "freno_a")   == 0) g_cal.freno_a   = clampf(valor, 0.0f, 30.0f);
    else if (strcmp(param, "anticipo")  == 0) g_cal.anticipo  = clampf(valor, 0.0f, 0.5f);
    else if (strcmp(param, "guardar")   == 0) return calib_guardar();
    else if (strcmp(param, "listar")    == 0) { calib_listar(); return true; }
    else return false;
    return true;
}

/* ─────────────────────────────────────────────────────────────────────────────
 * INICIALIZACIÓN DE HARDWARE
 * ──────────────────────────────────────────────────────────────────────────── */

static void uart_pi_init(void)
{
    const uart_config_t cfg = {
        .baud_rate  = PI_UART_BAUD,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };
    ESP_ERROR_CHECK(uart_driver_install(PI_UART_NUM,
                                        PI_UART_BUF_SIZE * 2,
                                        PI_UART_BUF_SIZE * 2,
                                        10, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(PI_UART_NUM, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(PI_UART_NUM,
                                 PI_UART_TX_PIN, PI_UART_RX_PIN,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    ESP_LOGI(TAG_PI, "UART Pi listo: TX=%d RX=%d @ %d baud",
             PI_UART_TX_PIN, PI_UART_RX_PIN, PI_UART_BAUD);
}

static void uart_vesc_init(void)
{
    const uart_config_t cfg = {
        .baud_rate  = VESC_UART_BAUD,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };
    ESP_ERROR_CHECK(uart_driver_install(VESC_UART_NUM,
                                        VESC_UART_BUF_SIZE * 2,
                                        VESC_UART_BUF_SIZE * 2,
                                        0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(VESC_UART_NUM, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(VESC_UART_NUM,
                                 VESC_UART_TX_PIN, VESC_UART_RX_PIN,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    ESP_LOGI(TAG_VESC, "UART VESC listo: TX=%d RX=%d @ %d baud",
             VESC_UART_TX_PIN, VESC_UART_RX_PIN, VESC_UART_BAUD);
}

static void led_init(void)
{
    gpio_reset_pin(STATUS_LED_PIN);
    gpio_set_direction(STATUS_LED_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(STATUS_LED_PIN, 0);
}

static void ir_init(void)
{
#if IR_SENSORS_ENABLED
    gpio_set_direction(IR_SENSOR_LEFT_PIN,  GPIO_MODE_INPUT);
    gpio_set_direction(IR_SENSOR_RIGHT_PIN, GPIO_MODE_INPUT);
    ESP_LOGI(TAG_MAIN, "Sensores IR activados: izq=%d der=%d",
             IR_SENSOR_LEFT_PIN, IR_SENSOR_RIGHT_PIN);
#endif
}

/** @brief true si algún sensor IR ve un obstáculo cerca.
 *         Con IR_SENSORS_ENABLED=0 siempre false (sensores sin cablear). */
static bool obstaculo_detectado(void)
{
#if IR_SENSORS_ENABLED
    int izq = gpio_get_level(IR_SENSOR_LEFT_PIN);
    int der = gpio_get_level(IR_SENSOR_RIGHT_PIN);
    return (izq == IR_OBSTACLE_LEVEL) || (der == IR_OBSTACLE_LEVEL);
#else
    return false;
#endif
}

/* ─────────────────────────────────────────────────────────────────────────────
 * INTERFAZ CON EL VESC — Protocolo binario oficial (firmware bldc de Vedder)
 *
 * Un paquete VESC por UART es:
 *   corto : [0x02][len_8][payload...][CRC16_hi][CRC16_lo][0x03]
 *   largo : [0x03][len_hi][len_lo][payload...][CRC16_hi][CRC16_lo][0x03]
 * El CRC16 (CCITT/XMODEM, poly 0x1021) se calcula sobre el payload.
 * Referencia: https://github.com/vedderb/bldc
 * ──────────────────────────────────────────────────────────────────────────── */

static uint16_t vesc_crc16(const uint8_t *buf, unsigned int len)
{
    uint16_t crc = 0;
    for (unsigned int i = 0; i < len; i++) {
        crc ^= (uint16_t)buf[i] << 8;
        for (int b = 0; b < 8; b++) {
            if (crc & 0x8000) crc = (uint16_t)((crc << 1) ^ 0x1021);
            else              crc = (uint16_t)(crc << 1);
        }
    }
    return crc;
}

/** @brief Arma y envía un paquete VESC (payload corto, < 256 bytes). */
static void vesc_send_packet(const uint8_t *payload, int len)
{
    uint8_t buf[64];
    int idx = 0;
    buf[idx++] = 0x02;
    buf[idx++] = (uint8_t)len;
    memcpy(&buf[idx], payload, len); idx += len;
    uint16_t crc = vesc_crc16(payload, (unsigned)len);
    buf[idx++] = (uint8_t)(crc >> 8);
    buf[idx++] = (uint8_t)(crc & 0xFF);
    buf[idx++] = 0x03;
    uart_write_bytes(VESC_UART_NUM, (const char *)buf, (size_t)idx);
}

static int append_i32(uint8_t *p, int i, int32_t v)
{
    p[i++] = (uint8_t)(v >> 24);
    p[i++] = (uint8_t)(v >> 16);
    p[i++] = (uint8_t)(v >> 8);
    p[i++] = (uint8_t)(v);
    return i;
}

/** @brief Manda un comando de un int32 al motor 1 (UART) o al 2 (por CAN). */
static void vesc_cmd_i32(uint8_t comm_id, int32_t valor, bool por_can)
{
    uint8_t p[10];
    int n = 0;
    if (por_can) {
        p[n++] = COMM_FORWARD_CAN;
        p[n++] = VESC_CAN_ID_MOTOR_2;
    }
    p[n++] = comm_id;
    n = append_i32(p, n, valor);
    vesc_send_packet(p, n);
}

/**
 * @brief Fija el duty de CADA rueda por separado. El corazón del robot diferencial.
 *
 * @param izq  Velocidad hacia adelante de la rueda izquierda [-1..1]
 * @param der  Velocidad hacia adelante de la rueda derecha   [-1..1]
 *
 * izq=+d, der=+d  → avanza derecho
 * izq=+d, der=-d  → gira sobre su propio eje (a la derecha)
 * izq=+d, der=0   → pivotea sobre la rueda derecha, describiendo un arco
 */
static void vesc_set_duty_lr(float izq, float der)
{
    izq = clampf(izq, -1.0f, 1.0f);
    der = clampf(der, -1.0f, 1.0f);

    vesc_cmd_i32(COMM_SET_DUTY,
                 (int32_t)(izq * MOTOR_IZQ_SIGNO * 100000.0f), false);
#if VESC_DUAL
    vesc_cmd_i32(COMM_SET_DUTY,
                 (int32_t)(der * MOTOR_DER_SIGNO * 100000.0f), true);
#endif
}

/**
 * @brief Freno ACTIVO en las dos ruedas.
 *
 * Esto es lo que antes faltaba. Poner duty=0 deja el motor en rueda libre y el
 * robot sigue de largo por inercia: por eso un giro de 90° terminaba en 106-110°.
 * COMM_SET_CURRENT_BRAKE inyecta corriente de frenado y lo clava.
 */
static void vesc_frenar(void)
{
    int32_t ma = (int32_t)(g_cal.freno_a * 1000.0f);
    vesc_cmd_i32(COMM_SET_CURRENT_BRAKE, ma, false);
#if VESC_DUAL
    vesc_cmd_i32(COMM_SET_CURRENT_BRAKE, ma, true);
#endif
}

/** @brief Pide a la VESC maestra su paquete de valores (incluye el tacómetro). */
static void vesc_pedir_valores(void)
{
    uint8_t p[1] = { COMM_GET_VALUES };
    vesc_send_packet(p, 1);
}

/* ─────────────────────────────────────────────────────────────────────────────
 * TAREA: lectura del VESC (odometría)
 *
 * El firmware viejo solo ESCRIBÍA a la VESC. Sin leerla no hay odometría, y sin
 * odometría todo giro es a ciegas y por cronómetro. Esta tarea arma los paquetes
 * que llegan por UART2 y saca de COMM_GET_VALUES el tacómetro y la tensión.
 * ──────────────────────────────────────────────────────────────────────────── */

/* Desplazamientos dentro del payload de COMM_GET_VALUES (firmware VESC 5.x/6.x).
 * OJO: en firmwares 3.x no existen avg_id/avg_iq y todo esto se corre 8 bytes.
 * Por eso se valida el largo antes de leer: si tu VESC es vieja, vas a ver el
 * aviso "GET_VALUES corto" en el monitor y la odometría se desactiva sola. */
#define GV_OFF_VIN    27
#define GV_OFF_TACH   45
#define GV_LEN_MINIMO 53

static int32_t leer_i32(const uint8_t *p, int off)
{
    return (int32_t)(((uint32_t)p[off]   << 24) |
                     ((uint32_t)p[off+1] << 16) |
                     ((uint32_t)p[off+2] << 8)  |
                     ((uint32_t)p[off+3]));
}

static int16_t leer_i16(const uint8_t *p, int off)
{
    return (int16_t)(((uint16_t)p[off] << 8) | (uint16_t)p[off+1]);
}

static void vesc_procesar_payload(const uint8_t *p, int len)
{
    if (len < 1) return;
    if (p[0] != COMM_GET_VALUES) return;      /* por ahora solo nos interesa este */

    if (len < GV_LEN_MINIMO) {
        static bool avisado = false;
        if (!avisado) {
            ESP_LOGW(TAG_VESC, "GET_VALUES corto (%d bytes): firmware VESC antiguo. "
                               "Odometría desactivada, los giros irán por tiempo.", len);
            avisado = true;
        }
        return;
    }

    int32_t tach = leer_i32(p, GV_OFF_TACH);
    float   vin  = leer_i16(p, GV_OFF_VIN) / 10.0f;

    portENTER_CRITICAL(&odo_mux);
    g_tach = tach;
    g_vbat = vin;
    g_odo_ts_us = esp_timer_get_time();
    g_odo_alguna_vez = true;
    portEXIT_CRITICAL(&odo_mux);
}

static void task_vesc_rx(void *pv)
{
    /* Máquina de enmarcado del protocolo VESC. */
    enum { E_START, E_LEN, E_PAYLOAD, E_CRC, E_END } estado = E_START;
    uint8_t  payload[256];
    int      len = 0, leidos = 0, len_bytes = 0, len_idx = 0;
    uint16_t crc_rx = 0;
    int      crc_idx = 0;
    uint8_t  b;

    ESP_LOGI(TAG_VESC, "Tarea de lectura VESC iniciada.");

    while (1) {
        if (uart_read_bytes(VESC_UART_NUM, &b, 1, pdMS_TO_TICKS(50)) <= 0) continue;

        switch (estado) {
        case E_START:
            if (b == 0x02)      { len_bytes = 1; len_idx = 0; len = 0; estado = E_LEN; }
            else if (b == 0x03) { len_bytes = 2; len_idx = 0; len = 0; estado = E_LEN; }
            break;

        case E_LEN:
            len = (len << 8) | b;
            if (++len_idx >= len_bytes) {
                if (len == 0 || len > (int)sizeof(payload)) { estado = E_START; break; }
                leidos = 0;
                estado = E_PAYLOAD;
            }
            break;

        case E_PAYLOAD:
            payload[leidos++] = b;
            if (leidos >= len) { crc_rx = 0; crc_idx = 0; estado = E_CRC; }
            break;

        case E_CRC:
            crc_rx = (uint16_t)((crc_rx << 8) | b);
            if (++crc_idx >= 2) estado = E_END;
            break;

        case E_END:
            if (b == 0x03 && crc_rx == vesc_crc16(payload, (unsigned)len)) {
                vesc_procesar_payload(payload, len);
            }
            estado = E_START;
            break;
        }
    }
}

/** @brief Copia segura del tacómetro. Devuelve false si el dato está viejo. */
static bool odo_leer(int32_t *tach, float *vbat)
{
    bool ok;
    portENTER_CRITICAL(&odo_mux);
    if (tach) *tach = g_tach;
    if (vbat) *vbat = g_vbat;
    ok = g_odo_alguna_vez &&
         ((esp_timer_get_time() - g_odo_ts_us) < (ODO_FRESCURA_MS * 1000));
    portEXIT_CRITICAL(&odo_mux);
    return ok;
}

/**
 * @brief Cuántas cuentas de tacómetro vale un giro de N grados sobre el eje.
 *
 * Girando sobre su propio eje, cada rueda recorre un arco  s = θ·(vía/2).
 * Vueltas de rueda = s / (2π·radio).  Cuentas = vueltas · cpr.
 *
 * Se usa el valor absoluto: solo interesa cuánto se movió, no hacia dónde.
 */
static float grados_a_cuentas(float grados)
{
    float th   = fabsf(grados) * (float)M_PI / 180.0f;      /* rad */
    float arco = th * (g_cal.via_mm / 2.0f);                /* mm  */
    float vueltas = arco / (2.0f * (float)M_PI * g_cal.radio_mm);
    return vueltas * g_cal.cpr;
}

/* ─────────────────────────────────────────────────────────────────────────────
 * PARSER DE COMANDOS
 * ──────────────────────────────────────────────────────────────────────────── */

static robot_command_t parse_command(const char *str)
{
    if (strcmp(str, "AVANZAR")    == 0) return CMD_AVANZAR;
    if (strcmp(str, "PARAR")      == 0) return CMD_PARAR;
    if (strcmp(str, "GIRAR_180")  == 0) return CMD_GIRAR_180;
    if (strcmp(str, "ACERCAR")    == 0) return CMD_ACERCAR;
    if (strcmp(str, "RETROCEDER") == 0) return CMD_RETROCEDER;
    if (strcmp(str, "PING")       == 0) return CMD_PING;
    /* Comandos con parámetros */
    if (strncmp(str, "AVANZAR_T:", 10) == 0) return CMD_AVANZAR_T;
    if (strncmp(str, "RODAR:",      6) == 0) return CMD_RODAR;
    if (strncmp(str, "MOV:",        4) == 0) return CMD_MOV;
    if (strncmp(str, "GIRO:",       5) == 0) return CMD_GIRO;
    if (strncmp(str, "PIVOTE:",     7) == 0) return CMD_PIVOTE;
    if (strncmp(str, "RUTINA:",     7) == 0) return CMD_RUTINA;
    if (strncmp(str, "MODO:",       5) == 0) return CMD_MODO;
    if (strncmp(str, "CAL:",        4) == 0) return CMD_CAL;
    return CMD_DESCONOCIDO;
}

/* ─────────────────────────────────────────────────────────────────────────────
 * TAREA: enlace con la Raspberry Pi (Core 1)
 *
 * Lee líneas del UART, las parsea y las encola. Responde ACK/ERR.
 * MOV: es la excepción: no se encola ni se contesta, se escribe directo en la
 * consigna de teleoperación. A 10 comandos por segundo, esperar un ACK por cada
 * uno agregaría un viaje de ida y vuelta al lazo del joystick, y una cola
 * llenándose de posiciones viejas del acelerador es justo lo que no queremos.
 * ──────────────────────────────────────────────────────────────────────────── */

static void task_pi_link(void *pvParameters)
{
    uint8_t  rx_byte;
    char     line_buf[PI_UART_BUF_SIZE];
    int      line_pos = 0;
    char     ack_buf[96];

    ESP_LOGI(TAG_PI, "Tarea de enlace con la Pi iniciada.");

    while (1) {
        int bytes_read = uart_read_bytes(PI_UART_NUM, &rx_byte, 1, pdMS_TO_TICKS(20));
        if (bytes_read <= 0) continue;
        if (rx_byte == '\r') continue;

        if (rx_byte != '\n') {
            if (line_pos < (int)(sizeof(line_buf) - 1)) {
                line_buf[line_pos++] = (char)rx_byte;
            } else {
                ESP_LOGW(TAG_PI, "Buffer overflow — línea descartada.");
                line_pos = 0;
            }
            continue;
        }

        /* ── Fin de línea: procesar ─────────────────────────────────────── */
        line_buf[line_pos] = '\0';
        line_pos = 0;
        if (strlen(line_buf) == 0) continue;

        robot_command_t cmd = parse_command(line_buf);

        /* ── MOV: camino rápido, sin cola y sin ACK ─────────────────────── */
        if (cmd == CMD_MOV) {
            float v = 0.0f, w = 0.0f;
            if (sscanf(line_buf, "MOV:%f:%f", &v, &w) == 2) {
                portENTER_CRITICAL(&teleop_mux);
                g_teleop_v = clampf(v, -1.0f, 1.0f);
                g_teleop_w = clampf(w, -1.0f, 1.0f);
                g_teleop_ts_us = esp_timer_get_time();
                portEXIT_CRITICAL(&teleop_mux);
            }
            continue;
        }

        if (cmd == CMD_PING) {
            pi_print("PONG\n");
            continue;
        }

        if (cmd == CMD_DESCONOCIDO) {
            pi_print("ERR:DESCONOCIDO\n");
            ESP_LOGW(TAG_PI, "Comando no reconocido: '%s'", line_buf);
            continue;
        }

        ESP_LOGI(TAG_PI, "← Pi: '%s'", line_buf);

        command_msg_t msg;
        memset(&msg, 0, sizeof(msg));
        msg.cmd = cmd;
        strncpy(msg.raw, line_buf, sizeof(msg.raw) - 1);
        msg.duracion_ms = AVANZAR_T_MS_DEFAULT;
        msg.duty        = AVANZAR_T_DUTY_DEFAULT;
        msg.k           = 0.0f;
        msg.lado        = +1;

        bool ok = true;

        switch (cmd) {
        case CMD_AVANZAR_T: {
            int ms_tmp = 0; float duty_tmp = 0.0f;
            if (sscanf(line_buf, "AVANZAR_T:%d:%f", &ms_tmp, &duty_tmp) == 2) {
                msg.duracion_ms = ms_tmp;
                msg.duty        = duty_tmp;
            }
            break;
        }
        case CMD_RODAR: {
            float duty_tmp = 0.0f;
            if (sscanf(line_buf, "RODAR:%f", &duty_tmp) == 1) msg.duty = duty_tmp;
            break;
        }
        case CMD_GIRO: {
            float g = 0.0f;
            if (sscanf(line_buf, "GIRO:%f", &g) == 1) msg.grados = g;
            else ok = false;
            break;
        }
        case CMD_PIVOTE: {
            /* PIVOTE:<I|D>:<grados>:<k> — k = velocidad de la rueda interna */
            char lado = 'D'; float g = 0.0f, k = 0.0f;
            if (sscanf(line_buf, "PIVOTE:%c:%f:%f", &lado, &g, &k) >= 2) {
                msg.lado   = (lado == 'I' || lado == 'i') ? -1 : +1;
                msg.grados = g;
                msg.k      = clampf(k, 0.0f, 1.0f);
            } else ok = false;
            break;
        }
        case CMD_RUTINA:
            strncpy(msg.arg, line_buf + 7, sizeof(msg.arg) - 1);
            break;
        case CMD_MODO: {
            const char *m = line_buf + 5;
            if      (strcmp(m, "AUTO")   == 0) msg.modo = MODO_AUTO;
            else if (strcmp(m, "TELEOP") == 0) msg.modo = MODO_TELEOP;
            else if (strcmp(m, "PAUSA")  == 0) msg.modo = MODO_PAUSA;
            else ok = false;
            break;
        }
        case CMD_CAL: {
            char param[24] = {0}; float valor = 0.0f;
            if (sscanf(line_buf, "CAL:%23[^:]:%f", param, &valor) == 2) {
                strncpy(msg.arg, param, sizeof(msg.arg) - 1);
                msg.valor = valor;
            } else ok = false;
            break;
        }
        default:
            break;
        }

        if (!ok) {
            /* El eco se acota: line_buf tiene 256 bytes y ack_buf 96. Un comando
             * legítimo no pasa de ~48 caracteres, así que 60 sobra. */
            snprintf(ack_buf, sizeof(ack_buf), "ERR:PARAMETROS:%.60s\n", line_buf);
            pi_print(ack_buf);
            continue;
        }

        /* CAL: se aplica acá mismo, no pasa por la máquina de movimiento. */
        if (cmd == CMD_CAL) {
            if (calib_set(msg.arg, msg.valor)) {
                snprintf(ack_buf, sizeof(ack_buf), "ACK:%.60s\n", line_buf);
            } else {
                snprintf(ack_buf, sizeof(ack_buf), "ERR:CAL_PARAM:%s\n", msg.arg);
            }
            pi_print(ack_buf);
            continue;
        }

        if (xQueueSend(cmd_queue, &msg, pdMS_TO_TICKS(100)) == pdTRUE) {
            snprintf(ack_buf, sizeof(ack_buf), "ACK:%.60s\n", line_buf);
        } else {
            snprintf(ack_buf, sizeof(ack_buf), "ERR:COLA_LLENA\n");
        }
        pi_print(ack_buf);
    }
}

/* ─────────────────────────────────────────────────────────────────────────────
 * TAREA: movimiento (Core 0)
 *
 * Una máquina de estados que late cada TICK_MS. Ningún estado bloquea: se entra,
 * se avanza un paso y se sale. Por eso un comando nuevo interrumpe al anterior
 * de inmediato, que es lo que hace seguro el manejo desde el celular.
 * ──────────────────────────────────────────────────────────────────────────── */

/* Estado interno de la máquina */
static mov_estado_t s_estado = MOV_FRENADO;
static float   s_duty_izq = 0.0f, s_duty_der = 0.0f;
static int64_t s_t_fin_ms = 0;         /* para estados temporizados */
static float   s_giro_objetivo = 0.0f; /* grados pedidos (con signo) */
static float   s_giro_cuentas = 0.0f;  /* cuentas de tacómetro objetivo */
static int32_t s_giro_tach0 = 0;
static bool    s_giro_por_odo = false;
static int64_t s_giro_t0_ms = 0;
static float   s_pivote_k = 0.0f;
static int     s_pivote_lado = +1;

/* Rutina en curso */
static const paso_t *s_rutina = NULL;
static int           s_rutina_i = 0;

/* motion_iniciar_paso y motion_paso_terminado se llaman entre sí (un paso que
 * termina arranca el siguiente), así que una de las dos va declarada antes. */
static void motion_paso_terminado(void);

static void motion_frenar(void)
{
    s_estado   = MOV_FRENADO;
    s_duty_izq = s_duty_der = 0.0f;
    s_rutina   = NULL;
    vesc_frenar();
}

/** @brief Arranca un giro sobre el eje. Positivo = horario (a la derecha). */
static void motion_iniciar_giro(float grados)
{
    s_giro_objetivo = grados;
    s_giro_cuentas  = grados_a_cuentas(grados);
    s_giro_t0_ms    = ahora_ms();
    s_giro_por_odo  = odo_leer(&s_giro_tach0, NULL);

    /* Plazo por tiempo. Si la odometría responde manda ella, pero este plazo
     * queda SIEMPRE como tope de seguridad: si la VESC deja de contestar a
     * mitad de un giro, el robot no se queda dando vueltas para siempre. */
    float ms = fabsf(grados) * g_cal.ms_grado;
    s_t_fin_ms = s_giro_t0_ms + (int64_t)(ms * 1.6f) + 300;

    float d = g_cal.giro_duty;
    if (grados < 0) d = -d;
    s_duty_izq =  d;      /* izquierda adelante, derecha atrás = gira a la derecha */
    s_duty_der = -d;
    s_estado   = MOV_GIRO;

    ESP_LOGI(TAG_MAIN, "GIRO %.1f° → %.0f cuentas (odo=%s)",
             (double)grados, (double)s_giro_cuentas,
             s_giro_por_odo ? "sí" : "no, por tiempo");
}

static void motion_iniciar_paso(void)
{
    if (!s_rutina) return;
    const paso_t *p = &s_rutina[s_rutina_i];

    switch (p->tipo) {
    case PASO_GIRO:
        motion_iniciar_giro(p->p1);
        break;
    case PASO_TIEMPO:
        s_duty_izq = s_duty_der = p->p2;
        s_t_fin_ms = ahora_ms() + (int64_t)p->p1;
        s_estado   = MOV_TEMPORIZADO;
        break;
    case PASO_ESPERA:
        s_duty_izq = s_duty_der = 0.0f;
        s_t_fin_ms = ahora_ms() + (int64_t)p->p1;
        s_estado   = MOV_TEMPORIZADO;
        break;
    case PASO_FIN:
    default:
        s_rutina = NULL;
        pi_print("EVT:RUTINA_FIN\n");
        /* Con la rutina ya en NULL, esto devuelve a teleoperación si el cliente
         * está manejando, o frena si el robot estaba en modo autónomo. */
        motion_paso_terminado();
        break;
    }
}

/**
 * @brief Avanza al siguiente paso de la rutina; si no hay rutina, termina.
 *
 * OJO con el caso de teleoperación: si el cliente aprieta "¡Bailá!" o "giro de
 * 90°" desde el celular, al terminar esa acción hay que VOLVER a teleoperación.
 * Si se cayera en MOV_FRENADO, los MOV: siguientes actualizarían la consigna
 * pero nadie la leería, y el joystick quedaría muerto hasta reiniciar. Se
 * vuelve con la consigna en cero: el robot espera quieto a que el cliente
 * mueva el dedo, no sale disparado con el último valor de antes del baile.
 */
static void motion_paso_terminado(void)
{
    if (s_rutina) {
        s_rutina_i++;
        motion_iniciar_paso();
        return;
    }

    if (g_modo == MODO_TELEOP) {
        portENTER_CRITICAL(&teleop_mux);
        g_teleop_v = g_teleop_w = 0.0f;
        portEXIT_CRITICAL(&teleop_mux);
        s_duty_izq = s_duty_der = 0.0f;
        s_estado = MOV_TELEOP;
        vesc_frenar();
        return;
    }

    motion_frenar();
}

static void motion_aplicar_comando(const command_msg_t *msg)
{
    /* Cualquier comando corta lo que estuviera haciendo, incluida una rutina. */
    if (msg->cmd != CMD_RUTINA) s_rutina = NULL;

    switch (msg->cmd) {

    case CMD_PARAR:
        motion_frenar();
        break;

    case CMD_RODAR:
        s_duty_izq = s_duty_der = msg->duty;
        s_estado = MOV_RODAR;
        break;

    case CMD_AVANZAR:
        s_duty_izq = s_duty_der = VESC_DUTY_AVANZAR;
        s_estado = MOV_RODAR;
        break;

    case CMD_ACERCAR:
        s_duty_izq = s_duty_der = VESC_DUTY_ACERCAR;
        s_estado = MOV_RODAR;
        break;

    case CMD_RETROCEDER:
        s_duty_izq = s_duty_der = VESC_DUTY_RETRO;
        s_estado = MOV_RODAR;
        break;

    case CMD_AVANZAR_T:
        s_duty_izq = s_duty_der = msg->duty;
        s_t_fin_ms = ahora_ms() + msg->duracion_ms;
        s_estado = MOV_TEMPORIZADO;
        break;

    case CMD_GIRAR_180:
        motion_iniciar_giro(180.0f);
        break;

    case CMD_GIRO:
        motion_iniciar_giro(msg->grados);
        break;

    case CMD_PIVOTE:
        /* Arco: la rueda de afuera va a giro_duty y la de adentro a k·giro_duty.
         * Con k=0 pivotea clavado sobre una rueda; con k=0.5 describe una curva
         * amplia y avanza mientras gira. */
        s_pivote_k    = msg->k;
        s_pivote_lado = msg->lado;
        motion_iniciar_giro(msg->grados);
        s_estado = MOV_PIVOTE;
        if (msg->lado > 0) {          /* pivote a la derecha: interna = derecha */
            s_duty_izq = g_cal.giro_duty;
            s_duty_der = g_cal.giro_duty * msg->k;
        } else {
            s_duty_izq = g_cal.giro_duty * msg->k;
            s_duty_der = g_cal.giro_duty;
        }
        break;

    case CMD_RUTINA:
        if      (strcmp(msg->arg, "BAILE")     == 0) s_rutina = RUTINA_BAILE;
        else if (strcmp(msg->arg, "SALUDO")    == 0) s_rutina = RUTINA_SALUDO;
        else if (strcmp(msg->arg, "GIRO_180")  == 0) s_rutina = RUTINA_GIRO_180;
        else { s_rutina = NULL; motion_frenar(); break; }
        s_rutina_i = 0;
        motion_iniciar_paso();
        break;

    case CMD_MODO:
        g_modo = (modo_t)msg->modo;
        if (g_modo == MODO_TELEOP) {
            /* Entra en teleoperación quieto y esperando: el robot no debe
             * arrancar solo por un MOV viejo que quedara en la variable. */
            portENTER_CRITICAL(&teleop_mux);
            g_teleop_v = g_teleop_w = 0.0f;
            g_teleop_ts_us = 0;
            portEXIT_CRITICAL(&teleop_mux);
            s_estado = MOV_TELEOP;
            s_duty_izq = s_duty_der = 0.0f;
        } else if (g_modo == MODO_PAUSA) {
            motion_frenar();
        }
        ESP_LOGI(TAG_MAIN, "Modo → %d", (int)g_modo);
        break;

    default:
        break;
    }
}

/** @brief Un tick del estado MOV_GIRO / MOV_PIVOTE. true si terminó. */
static bool motion_giro_tick(void)
{
    int64_t t = ahora_ms();

    /* Tope de seguridad por tiempo: vale siempre, con odometría o sin ella. */
    if (t >= s_t_fin_ms) return true;

    /* ¿Podemos cerrar el lazo? Si al arrancar no había odometría, le damos un
     * margen por si la VESC tardó en contestar el primer GET_VALUES. */
    int32_t tach;
    bool fresco = odo_leer(&tach, NULL);

    if (!s_giro_por_odo && fresco && (t - s_giro_t0_ms) < ODO_ESPERA_MS) {
        s_giro_tach0   = tach;
        s_giro_por_odo = true;
    }

    if (s_giro_por_odo && fresco) {
        float recorrido = fabsf((float)(tach - s_giro_tach0));
        /* Se corta un poco antes: aun con freno activo hay algo de inercia. */
        if (recorrido >= s_giro_cuentas * (1.0f - g_cal.anticipo)) return true;
        return false;
    }

    /* Sin odometría: giro a ciegas por tiempo. El plazo real es ms_grado·grados;
     * s_t_fin_ms es 1.6× eso y solo actúa de tope. */
    float ms_previstos = fabsf(s_giro_objetivo) * g_cal.ms_grado;
    return (t - s_giro_t0_ms) >= (int64_t)ms_previstos;
}

static void task_motion(void *pvParameters)
{
    command_msg_t msg;
    int tick = 0;

    ESP_LOGI(TAG_MAIN, "Tarea de movimiento iniciada.");
    vesc_set_duty_lr(0.0f, 0.0f);

    while (1) {
        /* ── 1. ¿Hay comando nuevo? Se atiende de inmediato ──────────────── */
        while (xQueueReceive(cmd_queue, &msg, 0) == pdTRUE) {
            ESP_LOGI(TAG_MAIN, "Ejecutando: %s", msg.raw);
            gpio_set_level(STATUS_LED_PIN, 1);
            motion_aplicar_comando(&msg);
            gpio_set_level(STATUS_LED_PIN, 0);
        }

        /* ── 2. Avanzar el estado actual ─────────────────────────────────── */
        switch (s_estado) {

        case MOV_TELEOP: {
            float v, w; int64_t ts;
            portENTER_CRITICAL(&teleop_mux);
            v = g_teleop_v; w = g_teleop_w; ts = g_teleop_ts_us;
            portEXIT_CRITICAL(&teleop_mux);

            /* DEADMAN: sin MOV fresco, se frena. */
            if (ts == 0 || (esp_timer_get_time() - ts) > (TELEOP_TIMEOUT_MS * 1000)) {
                if (s_duty_izq != 0.0f || s_duty_der != 0.0f) {
                    ESP_LOGW(TAG_MAIN, "Deadman: sin MOV fresco → freno.");
                    pi_print("EVT:DEADMAN\n");
                }
                s_duty_izq = s_duty_der = 0.0f;
                vesc_frenar();
                break;
            }

            /* Mezcla diferencial. Si la suma se pasa de 1 se normaliza en vez de
             * recortar, para que al acelerar y girar a la vez no se pierda el
             * giro (recortar cambia la proporción entre las dos ruedas). */
            float izq = v + w;
            float der = v - w;
            float m = fmaxf(fabsf(izq), fabsf(der));
            if (m > 1.0f) { izq /= m; der /= m; }

            if (obstaculo_detectado() && v > 0.0f) {
                izq = der = 0.0f;   /* no dejamos avanzar contra un obstáculo */
            }
            s_duty_izq = izq;
            s_duty_der = der;
            vesc_set_duty_lr(izq, der);
            break;
        }

        case MOV_TEMPORIZADO:
            if (obstaculo_detectado() && s_duty_izq > 0.0f && s_duty_der > 0.0f) {
                ESP_LOGW(TAG_MAIN, "¡Obstáculo! Deteniendo.");
                motion_paso_terminado();
                break;
            }
            if (ahora_ms() >= s_t_fin_ms) motion_paso_terminado();
            else vesc_set_duty_lr(s_duty_izq, s_duty_der);
            break;

        case MOV_GIRO:
        case MOV_PIVOTE:
            if (motion_giro_tick()) motion_paso_terminado();
            else                    vesc_set_duty_lr(s_duty_izq, s_duty_der);
            break;

        case MOV_RODAR:
            if (obstaculo_detectado() && s_duty_izq > 0.0f) {
                ESP_LOGW(TAG_MAIN, "¡Obstáculo! Deteniendo el rodado.");
                motion_frenar();
                break;
            }
            vesc_set_duty_lr(s_duty_izq, s_duty_der);
            break;

        case MOV_LIBRE:
            vesc_set_duty_lr(0.0f, 0.0f);
            break;

        case MOV_FRENADO:
        default:
            /* Se refresca el freno: la VESC lo suelta si no recibe nada en ~1 s. */
            vesc_frenar();
            break;
        }

        /* ── 3. Pedir odometría mientras haya un giro en curso ───────────── */
        if (s_estado == MOV_GIRO || s_estado == MOV_PIVOTE) {
            vesc_pedir_valores();
        } else if ((tick % 25) == 0) {
            vesc_pedir_valores();   /* 2 Hz en reposo: mantiene vbat al día */
        }

        /* ── 4. Telemetría a la Pi, 5 Hz ─────────────────────────────────── */
        if ((tick % (200 / TICK_MS)) == 0) {
            int32_t tach; float vbat;
            bool odo_ok = odo_leer(&tach, &vbat);
            char tel[96];
            snprintf(tel, sizeof(tel), "TEL:%d:%.2f:%.2f:%ld:%.1f:%c%c\n",
                     (int)g_modo, (double)s_duty_izq, (double)s_duty_der,
                     (long)tach, (double)vbat,
                     odo_ok ? 'O' : '-',
                     obstaculo_detectado() ? 'X' : '-');
            pi_print(tel);
        }

        tick++;
        vTaskDelay(pdMS_TO_TICKS(TICK_MS));
    }
}

/* ─────────────────────────────────────────────────────────────────────────────
 * PUNTO DE ENTRADA: app_main
 * ──────────────────────────────────────────────────────────────────────────── */

void app_main(void)
{
    ESP_LOGI(TAG_MAIN, "=========================================");
    ESP_LOGI(TAG_MAIN, " Robot Pingüino — ESP32 Firmware v2.0    ");
    ESP_LOGI(TAG_MAIN, " diferencial · no bloqueante · odometría ");
    ESP_LOGI(TAG_MAIN, "=========================================");

    /* ── 1. NVS (calibración persistida) ─────────────────────────────────── */
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    calib_cargar();

    /* ── 2. Hardware ─────────────────────────────────────────────────────── */
    led_init();
    ir_init();
    pi_tx_mutex = xSemaphoreCreateMutex();
    uart_pi_init();
    uart_vesc_init();

    /* ── 3. Parpadeo de inicio (3 veces = "listo") ───────────────────────── */
    for (int i = 0; i < 3; i++) {
        gpio_set_level(STATUS_LED_PIN, 1);
        vTaskDelay(pdMS_TO_TICKS(150));
        gpio_set_level(STATUS_LED_PIN, 0);
        vTaskDelay(pdMS_TO_TICKS(150));
    }

    /* ── 4. Cola de comandos ─────────────────────────────────────────────── */
    cmd_queue = xQueueCreate(10, sizeof(command_msg_t));
    if (cmd_queue == NULL || pi_tx_mutex == NULL) {
        ESP_LOGE(TAG_MAIN, "ERROR: no se pudo crear la cola o el mutex.");
        return;
    }

    /* ── 5. Tareas FreeRTOS ──────────────────────────────────────────────────
     * En el ESP32, core 0 = PRO_CPU y core 1 = APP_CPU (el comentario de la
     * versión anterior los tenía cambiados).
     *
     *   Core 0 (PRO_CPU): todo lo que habla con las llantas — el lazo de
     *     movimiento y la lectura del VESC. Se mantienen juntos para que el
     *     tiempo entre "leo el tacómetro" y "decido si freno" sea corto y parejo.
     *
     *   Core 1 (APP_CPU): el enlace con la Raspberry Pi. Aislado a propósito:
     *     una ráfaga de comandos de la Pi no puede robarle tiempo al lazo que
     *     controla los motores.
     * ────────────────────────────────────────────────────────────────────── */
    xTaskCreatePinnedToCore(task_motion,  "motion",   4096, NULL, 6, NULL, 0);
    xTaskCreatePinnedToCore(task_vesc_rx, "vesc_rx",  3072, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(task_pi_link, "pi_link",  4096, NULL, 5, NULL, 1);

    ESP_LOGI(TAG_MAIN, "Sistema listo. Esperando comandos de la Raspberry Pi…");
    pi_print("EVT:LISTO\n");
}

/* =============================================================================
 * CÓMO CALIBRAR (con las RUEDAS EN EL AIRE y el kill switch a mano)
 * =============================================================================
 *
 * Todo esto se hace por el monitor serie o desde la Pi, sin reflashear. Al
 * terminar, `CAL:guardar:1` lo deja grabado en NVS.
 *
 * 1) SENTIDO DE LAS RUEDAS
 *    Mandá  MOV:0.2:0  → las dos ruedas deben girar hacia ADELANTE.
 *    Si el robot gira en vez de avanzar, una rueda está espejada: cambiá
 *    MOTOR_DER_SIGNO a -1.0f (esto sí es recompilar, es lo único).
 *    Mandá  MOV:0:0.2  → debe girar sobre su eje HACIA LA DERECHA.
 *
 * 2) CUENTAS POR VUELTA (cpr) — solo si la VESC contesta
 *    Mirá la telemetría: TEL:...:<tach>:...:O   ← la O final significa que la
 *    odometría está viva. Si ves un guion, el RX del VESC no está llegando
 *    (revisá GPIO5 ← TX del VESC y la masa común).
 *    Con la odometría viva: anotá el tach, girá la rueda maestra EXACTAMENTE
 *    una vuelta a mano, anotá de nuevo. La diferencia es tu cpr.
 *        CAL:cpr:<diferencia>
 *
 * 3) GEOMETRÍA
 *    Medí con una cinta y cargá los valores reales:
 *        CAL:radio_mm:84        (rueda hub de 168 mm de diámetro)
 *        CAL:via_mm:300         (centro a centro de las dos ruedas)
 *
 * 4) EL GIRO DE 90° — el que estaba fallando
 *    a) Primero probá con odometría:  GIRO:90  cuatro veces seguidas.
 *       Si vuelve al punto de partida, listo, no hace falta tocar ms_grado.
 *    b) Si la odometría no está viva, el giro va por tiempo y hay que ajustar
 *       ms_grado. Mandá GIRO:90, medí con transportador o cinta en el piso:
 *           ms_grado_nuevo = ms_grado_actual × (90 / grados_medidos)
 *       Con los 108° que medía el equipo:  7.2 × (90/108) = 6.0
 *       Repetí hasta que 90 sea 90.
 *
 * 5) FRENO
 *    Si al terminar el giro todavía se pasa un poco, subí la corriente de freno:
 *        CAL:freno_a:6
 *    Si en cambio se clava de golpe y el robot se sacude, bajala.
 *    El `anticipo` (por defecto 0.10 = corta al 90% del giro) es el ajuste fino.
 *
 * 6) GUARDAR
 *        CAL:guardar:1
 *        CAL:listar:0     ← para verificar que quedó
 *
 * -----------------------------------------------------------------------------
 * PRUEBA DEL DEADMAN — no saltear antes de dejar que alguien maneje del celular
 * -----------------------------------------------------------------------------
 *    Mandá  MODO:TELEOP  y después  MOV:0.3:0
 *    Las ruedas arrancan. DEJÁ DE MANDAR comandos.
 *    A los ~400 ms deben frenar solas y aparecer  EVT:DEADMAN  en el monitor.
 *    Si siguen girando, no conectes el celular: revisá TELEOP_TIMEOUT_MS.
 * =============================================================================
 */
