/**
 * =============================================================================
 * ROBOT BÍPEDO — Firmware ESP32 (ESP-IDF, C puro, sin Arduino)
 * =============================================================================
 * Función   : Recibe comandos de movimiento de la Raspberry Pi por UART,
 *             actúa sobre el controlador VESC (vía UART secundario) y
 *             responde con ACK o ERR.
 *
 * Protocolo UART (Pi → ESP32, ASCII orientado a líneas):
 *   Recibe  : "AVANZAR\n" | "PARAR\n" | "GIRAR_180\n"
 *             "ACERCAR\n" | "RETROCEDER\n"
 *   Responde: "ACK:<COMANDO>\n"   si el comando fue reconocido
 *             "ERR:DESCONOCIDO\n" si el comando no existe
 *
 * Hardware:
 *   UART0 (GPIO 1/3)  → Consola de depuración (monitor serie)
 *   UART1 (GPIO 16/17)→ Raspberry Pi (comandos de control)
 *   UART2 (GPIO 4/5)  → VESC (protocolo VESC UART simplificado)
 *
 * Herramienta de build: ESP-IDF v5.x
 *   Instalar : https://docs.espressif.com/projects/esp-idf/en/stable/
 *   Compilar : idf.py build
 *   Flashear : idf.py -p /dev/ttyUSB0 flash monitor
 * =============================================================================
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"

/* ─────────────────────────────────────────────────────────────────────────────
 * CONFIGURACIÓN DE PINES Y UART
 * ──────────────────────────────────────────────────────────────────────────── */

/* UART1: comunicación con la Raspberry Pi */
#define PI_UART_NUM         UART_NUM_1
#define PI_UART_TX_PIN      17          /* GPIO 17 → Pi RX */
#define PI_UART_RX_PIN      16          /* GPIO 16 ← Pi TX */
#define PI_UART_BAUD        115200
#define PI_UART_BUF_SIZE    256

/* UART2: comunicación con el VESC */
#define VESC_UART_NUM       UART_NUM_2
#define VESC_UART_TX_PIN    4           /* GPIO 4 → VESC RX  */
#define VESC_UART_RX_PIN    5           /* GPIO 5 ← VESC TX  */
#define VESC_UART_BAUD      115200
#define VESC_UART_BUF_SIZE  256

/* LED de estado (LED azul integrado en muchas placas ESP32 = GPIO 2) */
#define STATUS_LED_PIN      GPIO_NUM_2

/* Parámetros de movimiento (ajustar según tu VESC y mecánica) */
#define VESC_DUTY_AVANZAR   0.25f   /* 25 % del ciclo de trabajo */
#define VESC_DUTY_ACERCAR   0.12f   /* 12 % — velocidad lenta    */
#define VESC_DUTY_RETRO    -0.20f   /* Negativo = retroceso      */
#define VESC_DUTY_STOP      0.00f
#define GIRO_180_MS         2000    /* Tiempo estimado para 180° */

/* ── Controlador VESC (protocolo binario oficial por UART) ────────────────────
 * Controlador dual: el ESP32 habla por UART a UNA VESC (maestra) y le manda al
 * segundo motor por CAN (la maestra reenvía). Ajustá según tu VESC Tool:        */
#define VESC_DUAL              1   /* 1 = dos motores (uno por UART, otro por CAN) */
#define VESC_CAN_ID_MOTOR_2    25  /* CAN ID de la 2ª VESC (VESC Tool → App → Controller ID) */
#define VESC_INVERTIR_MOTOR_2  0   /* 1 si el robot GIRA en vez de ir derecho (motores enfrentados) */

/* IDs de comando del protocolo VESC (de datatypes.h del firmware bldc) */
#define COMM_SET_DUTY          5
#define COMM_FORWARD_CAN       34

/* Valores por defecto para AVANZAR_T si la Pi no manda parámetros */
#define AVANZAR_T_MS_DEFAULT    5000    /* 5 segundos */
#define AVANZAR_T_DUTY_DEFAULT  0.25f   /* 25 % */

/* ── Sensores infrarrojos (obstáculos) — a futuro ─────────────────────────────
 * Poné IR_SENSORS_ENABLED en 1 cuando cablees los sensores.
 * La mayoría de sensores IR de obstáculo (tipo FC-51) dan nivel BAJO (0) al
 * detectar algo cerca; ajustá IR_OBSTACLE_LEVEL según tu sensor.
 * Nota: GPIO 34/35 son solo-entrada en el ESP32 (sin pull interno), ideales
 * para leer la salida digital de estos sensores.                              */
#define IR_SENSORS_ENABLED   0
#define IR_SENSOR_LEFT_PIN   GPIO_NUM_34
#define IR_SENSOR_RIGHT_PIN  GPIO_NUM_35
#define IR_OBSTACLE_LEVEL    0

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
    CMD_DESCONOCIDO
} robot_command_t;

typedef struct {
    robot_command_t cmd;
    char            raw[32];       /* Texto original del comando */
    int             duracion_ms;   /* Para AVANZAR_T: cuánto avanzar */
    float           duty;          /* Para AVANZAR_T: velocidad [-1..1] */
} command_msg_t;

/* ─────────────────────────────────────────────────────────────────────────────
 * VARIABLES GLOBALES
 * ──────────────────────────────────────────────────────────────────────────── */

static const char *TAG_MAIN  = "ROBOT_MAIN";
static const char *TAG_PI    = "UART_PI";
static const char *TAG_VESC  = "UART_VESC";

static QueueHandle_t cmd_queue;     /* Cola de comandos Pi → tarea de control */

/* ─────────────────────────────────────────────────────────────────────────────
 * INICIALIZACIÓN DE HARDWARE
 * ──────────────────────────────────────────────────────────────────────────── */

/**
 * @brief Inicializa el UART hacia la Raspberry Pi.
 */
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
                                        PI_UART_BUF_SIZE * 2,  /* RX buffer */
                                        PI_UART_BUF_SIZE * 2,  /* TX buffer */
                                        10,                     /* Cola de eventos */
                                        NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(PI_UART_NUM, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(PI_UART_NUM,
                                 PI_UART_TX_PIN,
                                 PI_UART_RX_PIN,
                                 UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE));
    ESP_LOGI(TAG_PI, "UART Pi listo: TX=%d RX=%d @ %d baud",
             PI_UART_TX_PIN, PI_UART_RX_PIN, PI_UART_BAUD);
}

/**
 * @brief Inicializa el UART hacia el VESC.
 */
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
                                 VESC_UART_TX_PIN,
                                 VESC_UART_RX_PIN,
                                 UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE));
    ESP_LOGI(TAG_VESC, "UART VESC listo: TX=%d RX=%d @ %d baud",
             VESC_UART_TX_PIN, VESC_UART_RX_PIN, VESC_UART_BAUD);
}

/**
 * @brief Inicializa el LED de estado.
 */
static void led_init(void)
{
    gpio_reset_pin(STATUS_LED_PIN);
    gpio_set_direction(STATUS_LED_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(STATUS_LED_PIN, 0);
}

/**
 * @brief Inicializa los pines de los sensores IR de obstáculo (a futuro).
 *        No hace nada si IR_SENSORS_ENABLED está en 0.
 */
static void ir_init(void)
{
#if IR_SENSORS_ENABLED
    gpio_set_direction(IR_SENSOR_LEFT_PIN,  GPIO_MODE_INPUT);
    gpio_set_direction(IR_SENSOR_RIGHT_PIN, GPIO_MODE_INPUT);
    ESP_LOGI(TAG_MAIN, "Sensores IR activados: izq=%d der=%d",
             IR_SENSOR_LEFT_PIN, IR_SENSOR_RIGHT_PIN);
#endif
}

/**
 * @brief Devuelve true si algún sensor IR detecta un obstáculo cercano.
 *        Con IR_SENSORS_ENABLED=0 siempre devuelve false (sensores no cableados).
 */
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
 * Un paquete VESC por UART es:
 *   [0x02][len][payload...][CRC16_hi][CRC16_lo][0x03]
 * El CRC16 (CCITT/XMODEM, poly 0x1021) se calcula sobre el payload.
 * Referencia: https://github.com/vedderb/bldc
 * ──────────────────────────────────────────────────────────────────────────── */

/** @brief CRC16 CCITT (XMODEM) usado por el protocolo VESC. */
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
    buf[idx++] = 0x02;                       /* start (paquete corto) */
    buf[idx++] = (uint8_t)len;               /* longitud del payload  */
    memcpy(&buf[idx], payload, len); idx += len;
    uint16_t crc = vesc_crc16(payload, (unsigned)len);
    buf[idx++] = (uint8_t)(crc >> 8);
    buf[idx++] = (uint8_t)(crc & 0xFF);
    buf[idx++] = 0x03;                       /* end */
    uart_write_bytes(VESC_UART_NUM, (const char *)buf, (size_t)idx);
}

/** @brief Carga un duty (int32 = duty*100000, big-endian) en el payload. */
static int vesc_append_set_duty(uint8_t *p, int i, float duty)
{
    int32_t d = (int32_t)(duty * 100000.0f);
    p[i++] = COMM_SET_DUTY;
    p[i++] = (uint8_t)(d >> 24);
    p[i++] = (uint8_t)(d >> 16);
    p[i++] = (uint8_t)(d >> 8);
    p[i++] = (uint8_t)(d);
    return i;
}

/**
 * @brief Fija el duty en AMBOS motores del controlador dual.
 *        Motor 1: por UART directo a la VESC maestra.
 *        Motor 2: la maestra lo reenvía por CAN a la esclava (COMM_FORWARD_CAN).
 *
 * @param duty  Ciclo de trabajo [-1.0 .. 1.0]. + = adelante, 0 = parar.
 */
static void vesc_set_duty(float duty)
{
    if (duty >  1.0f) duty =  1.0f;
    if (duty < -1.0f) duty = -1.0f;

    /* Motor 1 (VESC conectada por UART) */
    uint8_t p1[8];
    int n1 = vesc_append_set_duty(p1, 0, duty);
    vesc_send_packet(p1, n1);

#if VESC_DUAL
    /* Motor 2 (VESC por CAN): [COMM_FORWARD_CAN][can_id][COMM_SET_DUTY][int32] */
    float duty2 = VESC_INVERTIR_MOTOR_2 ? -duty : duty;
    uint8_t p2[8];
    int n2 = 0;
    p2[n2++] = COMM_FORWARD_CAN;
    p2[n2++] = VESC_CAN_ID_MOTOR_2;
    n2 = vesc_append_set_duty(p2, n2, duty2);
    vesc_send_packet(p2, n2);
#endif

    ESP_LOGD(TAG_VESC, "→ VESC duty=%.3f", (double)duty);
}

/* ─────────────────────────────────────────────────────────────────────────────
 * PARSER DE COMANDOS
 * ──────────────────────────────────────────────────────────────────────────── */

/**
 * @brief Convierte un string de comando a su enumeración.
 */
static robot_command_t parse_command(const char *str)
{
    if (strcmp(str, "AVANZAR")    == 0) return CMD_AVANZAR;
    if (strcmp(str, "PARAR")      == 0) return CMD_PARAR;
    if (strcmp(str, "GIRAR_180")  == 0) return CMD_GIRAR_180;
    if (strcmp(str, "ACERCAR")    == 0) return CMD_ACERCAR;
    if (strcmp(str, "RETROCEDER") == 0) return CMD_RETROCEDER;
    /* Comandos con parámetros */
    if (strncmp(str, "AVANZAR_T:", 10) == 0) return CMD_AVANZAR_T;
    if (strncmp(str, "RODAR:", 6) == 0)      return CMD_RODAR;
    return CMD_DESCONOCIDO;
}

/* ─────────────────────────────────────────────────────────────────────────────
 * TAREA FreeRTOS: Recepción de comandos desde la Pi
 * ──────────────────────────────────────────────────────────────────────────── */

/**
 * @brief Lee líneas del UART de la Pi, parsea el comando y lo encola.
 *        También envía el ACK/ERR de vuelta.
 */
static void task_pi_receiver(void *pvParameters)
{
    uint8_t  rx_byte;
    char     line_buf[PI_UART_BUF_SIZE];
    int      line_pos = 0;
    char     ack_buf[64];

    ESP_LOGI(TAG_PI, "Tarea receptora Pi iniciada.");

    while (1) {
        /* Leer byte a byte hasta '\n' */
        int bytes_read = uart_read_bytes(PI_UART_NUM, &rx_byte, 1,
                                         pdMS_TO_TICKS(20));
        if (bytes_read <= 0) {
            continue;  /* Timeout: no hay datos, seguir esperando */
        }

        if (rx_byte == '\r') {
            continue;  /* Ignorar CR */
        }

        if (rx_byte == '\n') {
            /* Fin de línea: procesar comando */
            line_buf[line_pos] = '\0';
            line_pos = 0;

            if (strlen(line_buf) == 0) {
                continue;
            }

            ESP_LOGI(TAG_PI, "← Pi: '%s'", line_buf);

            robot_command_t cmd = parse_command(line_buf);

            command_msg_t msg;
            msg.cmd = cmd;
            strncpy(msg.raw, line_buf, sizeof(msg.raw) - 1);
            msg.raw[sizeof(msg.raw) - 1] = '\0';
            /* Valores por defecto para AVANZAR_T */
            msg.duracion_ms = AVANZAR_T_MS_DEFAULT;
            msg.duty        = AVANZAR_T_DUTY_DEFAULT;

            if (cmd == CMD_AVANZAR_T) {
                /* Extraer parámetros: "AVANZAR_T:<ms>:<duty>" */
                int   ms_tmp   = 0;
                float duty_tmp = 0.0f;
                if (sscanf(line_buf, "AVANZAR_T:%d:%f", &ms_tmp, &duty_tmp) == 2) {
                    msg.duracion_ms = ms_tmp;
                    msg.duty        = duty_tmp;
                }
                /* Si el formato falla, quedan los valores por defecto (5 s, 25 %) */
            } else if (cmd == CMD_RODAR) {
                /* Extraer velocidad: "RODAR:<duty>" */
                float duty_tmp = 0.0f;
                if (sscanf(line_buf, "RODAR:%f", &duty_tmp) == 1) {
                    msg.duty = duty_tmp;
                }
            }

            if (cmd != CMD_DESCONOCIDO) {
                /* Encolar para la tarea de control */
                xQueueSend(cmd_queue, &msg, pdMS_TO_TICKS(100));

                /* ACK inmediato a la Pi */
                int ack_len = snprintf(ack_buf, sizeof(ack_buf),
                                       "ACK:%s\n", line_buf);
                uart_write_bytes(PI_UART_NUM, ack_buf, (size_t)ack_len);
                ESP_LOGI(TAG_PI, "→ Pi: %s", ack_buf);
            } else {
                /* Comando desconocido */
                int err_len = snprintf(ack_buf, sizeof(ack_buf),
                                       "ERR:DESCONOCIDO\n");
                uart_write_bytes(PI_UART_NUM, ack_buf, (size_t)err_len);
                ESP_LOGW(TAG_PI, "Comando no reconocido: '%s'", line_buf);
            }

        } else {
            /* Acumular byte en el buffer */
            if (line_pos < (int)(sizeof(line_buf) - 1)) {
                line_buf[line_pos++] = (char)rx_byte;
            } else {
                /* Overflow: descartar y reiniciar */
                ESP_LOGW(TAG_PI, "Buffer overflow — línea descartada.");
                line_pos = 0;
            }
        }
    }
}

/* ─────────────────────────────────────────────────────────────────────────────
 * TAREA FreeRTOS: Control del robot (VESC + lógica de movimiento)
 * ──────────────────────────────────────────────────────────────────────────── */

/**
 * @brief Toma comandos de la cola y los traduce en acciones de movimiento.
 */
static void task_robot_control(void *pvParameters)
{
    command_msg_t msg;
    float modo_duty = 0.0f;   /* Duty continuo a mantener (rolling); 0 = parado */

    ESP_LOGI(TAG_MAIN, "Tarea de control iniciada.");
    vesc_set_duty(0.0f);

    while (1) {
        /* Esperar un comando hasta 50 ms. Ese timeout también sirve de "tick":
         * en cada vuelta se reenvía el duty actual, así el rodado continuo NO se
         * corta (la VESC frena el motor si no recibe comandos por ~1 s). */
        if (xQueueReceive(cmd_queue, &msg, pdMS_TO_TICKS(50)) == pdTRUE) {
            ESP_LOGI(TAG_MAIN, "Ejecutando: %s", msg.raw);
            gpio_set_level(STATUS_LED_PIN, 1);

            switch (msg.cmd) {

                case CMD_RODAR:
                    /* Rodar continuo (modo atracción): mantiene este duty */
                    modo_duty = msg.duty;
                    ESP_LOGI(TAG_MAIN, "RODAR continuo a duty %.2f", (double)modo_duty);
                    break;

                case CMD_PARAR:
                    modo_duty = 0.0f;
                    break;

                case CMD_AVANZAR:
                    modo_duty = VESC_DUTY_AVANZAR;   /* rueda continuo (compat.) */
                    break;

                case CMD_ACERCAR:
                    modo_duty = VESC_DUTY_ACERCAR;
                    break;

                case CMD_RETROCEDER:
                    modo_duty = VESC_DUTY_RETRO;
                    break;

                case CMD_AVANZAR_T: {
                    /* Acción temporizada: aplica el duty (puede ser NEGATIVO =
                     * giro/retroceso contrario) por N ms y luego queda PARADO.
                     * Reenvía cada 50 ms y se frena si un sensor IR ve obstáculo. */
                    int t = 0;
                    ESP_LOGI(TAG_MAIN, "AVANZAR_T: %d ms a duty %.2f",
                             msg.duracion_ms, (double)msg.duty);
                    while (t < msg.duracion_ms) {
                        if (obstaculo_detectado()) {
                            ESP_LOGW(TAG_MAIN, "¡Obstáculo! Deteniendo.");
                            break;
                        }
                        vesc_set_duty(msg.duty);
                        vTaskDelay(pdMS_TO_TICKS(50));
                        t += 50;
                    }
                    modo_duty = 0.0f;   /* al terminar, queda parado */
                    break;
                }

                case CMD_GIRAR_180: {
                    /* Giro simplificado: retrocede GIRO_180_MS y queda parado. */
                    int t = 0;
                    while (t < GIRO_180_MS) {
                        vesc_set_duty(VESC_DUTY_RETRO);
                        vTaskDelay(pdMS_TO_TICKS(50));
                        t += 50;
                    }
                    modo_duty = 0.0f;
                    break;
                }

                case CMD_DESCONOCIDO:
                default:
                    ESP_LOGW(TAG_MAIN, "Comando desconocido — ignorado.");
                    break;
            }
            gpio_set_level(STATUS_LED_PIN, 0);
        }

        /* Tick: reenviar el duty continuo actual (rodando o parado) */
        vesc_set_duty(modo_duty);
    }
}

/* ─────────────────────────────────────────────────────────────────────────────
 * PUNTO DE ENTRADA: app_main
 * ──────────────────────────────────────────────────────────────────────────── */

void app_main(void)
{
    ESP_LOGI(TAG_MAIN, "=========================================");
    ESP_LOGI(TAG_MAIN, " Robot Bípedo — ESP32 Firmware v1.0     ");
    ESP_LOGI(TAG_MAIN, "=========================================");

    /* ── 1. Inicializar hardware ─────────────────────────────────────────── */
    led_init();
    ir_init();
    uart_pi_init();
    uart_vesc_init();

    /* ── 2. Parpadeo de inicio (3 veces = "listo") ───────────────────────── */
    for (int i = 0; i < 3; i++) {
        gpio_set_level(STATUS_LED_PIN, 1);
        vTaskDelay(pdMS_TO_TICKS(150));
        gpio_set_level(STATUS_LED_PIN, 0);
        vTaskDelay(pdMS_TO_TICKS(150));
    }

    /* ── 3. Crear cola de comandos ───────────────────────────────────────── */
    cmd_queue = xQueueCreate(10, sizeof(command_msg_t));
    if (cmd_queue == NULL) {
        ESP_LOGE(TAG_MAIN, "ERROR: No se pudo crear la cola de comandos.");
        return;
    }

    /* ── 4. Lanzar tareas FreeRTOS ───────────────────────────────────────── */
    /*
     * task_pi_receiver: alta prioridad, lee UART constantemente.
     *   Stack: 4096 bytes (suficiente para el buffer y snprintf)
     *   Core: 0 (APP_CPU)
     *
     * task_robot_control: prioridad media, ejecuta acciones de movimiento.
     *   Stack: 4096 bytes
     *   Core: 1 (PRO_CPU) — separado del receptor para evitar bloqueos
     */
    xTaskCreatePinnedToCore(
        task_pi_receiver,       /* Función de tarea          */
        "pi_receiver",          /* Nombre (debug)            */
        4096,                   /* Stack en bytes            */
        NULL,                   /* Parámetros                */
        5,                      /* Prioridad (0-24, mayor=más alto) */
        NULL,                   /* Handle (no necesitamos guardarlo) */
        0                       /* Core 0                    */
    );

    xTaskCreatePinnedToCore(
        task_robot_control,
        "robot_ctrl",
        4096,
        NULL,
        4,
        NULL,
        1                       /* Core 1                    */
    );

    ESP_LOGI(TAG_MAIN, "Sistema listo. Esperando comandos de la Raspberry Pi…");

    /* app_main retorna: FreeRTOS toma el control con las tareas creadas. */
}
