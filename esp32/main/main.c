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

/* ─────────────────────────────────────────────────────────────────────────────
 * TIPOS Y ENUMERACIONES
 * ──────────────────────────────────────────────────────────────────────────── */

typedef enum {
    CMD_AVANZAR = 0,
    CMD_PARAR,
    CMD_GIRAR_180,
    CMD_ACERCAR,
    CMD_RETROCEDER,
    CMD_DESCONOCIDO
} robot_command_t;

typedef struct {
    robot_command_t cmd;
    char            raw[32];   /* Texto original del comando */
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
        .source_clk = UART_SCLK_DEFAULT,
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
        .source_clk = UART_SCLK_DEFAULT,
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

/* ─────────────────────────────────────────────────────────────────────────────
 * INTERFAZ CON EL VESC
 * Nota: El VESC puede recibir comandos por UART usando su protocolo binario
 * (bldc_interface). Aquí se implementa una versión ASCII simplificada que
 * muchos firmwares VESC personalizados soportan.  Para el protocolo binario
 * oficial ver: https://github.com/vedderb/bldc
 * ──────────────────────────────────────────────────────────────────────────── */

/**
 * @brief Envía un comando de duty cycle al VESC vía UART.
 *        Formato ASCII sencillo: "SET_DUTY <valor>\n"
 *        Ajusta según el firmware de tu VESC.
 *
 * @param duty  Ciclo de trabajo [-1.0 .. 1.0].
 *              Positivo = adelante, negativo = atrás, 0 = parar.
 */
static void vesc_set_duty(float duty)
{
    char buf[64];
    /* Clamp para no exceder rango */
    if (duty >  1.0f) duty =  1.0f;
    if (duty < -1.0f) duty = -1.0f;

    int len = snprintf(buf, sizeof(buf), "SET_DUTY %.4f\n", (double)duty);
    uart_write_bytes(VESC_UART_NUM, buf, (size_t)len);
    ESP_LOGD(TAG_VESC, "→ VESC: %s", buf);
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

    ESP_LOGI(TAG_MAIN, "Tarea de control iniciada. Esperando comandos…");

    /* Estado inicial: parado */
    vesc_set_duty(VESC_DUTY_STOP);

    while (1) {
        /* Bloquear hasta recibir un comando (sin timeout = espera indefinida) */
        if (xQueueReceive(cmd_queue, &msg, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        ESP_LOGI(TAG_MAIN, "Ejecutando: %s", msg.raw);
        gpio_set_level(STATUS_LED_PIN, 1);   /* LED on durante acción */

        switch (msg.cmd) {

            case CMD_AVANZAR:
                vesc_set_duty(VESC_DUTY_AVANZAR);
                break;

            case CMD_PARAR:
                vesc_set_duty(VESC_DUTY_STOP);
                break;

            case CMD_ACERCAR:
                vesc_set_duty(VESC_DUTY_ACERCAR);
                break;

            case CMD_RETROCEDER:
                vesc_set_duty(VESC_DUTY_RETRO);
                break;

            case CMD_GIRAR_180:
                /*
                 * Estrategia de giro 180°:
                 * El bípedo gira aplicando duty positivo a una rueda/pierna
                 * y negativo a la otra. Aquí se implementa de forma simplificada
                 * enviando un comando de "GIRO" al VESC durante GIRO_180_MS ms.
                 * Ajusta GIRO_180_MS experimentalmente según la tracción.
                 */
                vesc_set_duty(VESC_DUTY_STOP);
                vTaskDelay(pdMS_TO_TICKS(100));

                /* Enviar comando de giro (formato depende de tu VESC firmware) */
                {
                    const char *giro_cmd = "GIRO_DERECHA\n";
                    uart_write_bytes(VESC_UART_NUM, giro_cmd, strlen(giro_cmd));
                }
                vTaskDelay(pdMS_TO_TICKS(GIRO_180_MS));

                /* Detener giro */
                vesc_set_duty(VESC_DUTY_STOP);
                vTaskDelay(pdMS_TO_TICKS(200));

                /* Continuar avanzando después del giro */
                vesc_set_duty(VESC_DUTY_AVANZAR);
                break;

            case CMD_DESCONOCIDO:
            default:
                ESP_LOGW(TAG_MAIN, "Comando desconocido en cola — ignorado.");
                break;
        }

        gpio_set_level(STATUS_LED_PIN, 0);   /* LED off */
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
