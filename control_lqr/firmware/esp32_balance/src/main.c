/* =============================================================================
 * ROBOT BALANCIN - Firmware ESP32 (ESP-IDF, C puro)
 * -----------------------------------------------------------------------------
 * El ESP32 es el cerebro del balanceo:
 *     MPU6050 --I2C--> [filtro + realimentacion de estado] --UART--> VESC
 * La Raspberry solo manda ordenes de alto nivel y recibe telemetria: NUNCA
 * esta dentro del lazo (Linux con GUI tiene jitter de decenas de ms y esta
 * planta se cae en ~113 ms).
 *
 * Arranca SIEMPRE desarmado y con la telemetria apagada. Para moverse hay
 * que mandar START explicitamente.
 *
 * Compilar:  pio run          Flashear:  pio run -t upload
 * Monitor :  pio device monitor
 * ========================================================================== */
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"

#include "config.h"
#include "mpu6050.h"
#include "vesc.h"
#include "balance.h"
#include "link.h"

static TaskHandle_t s_tarea_control;

/* Disparo periodico del lazo. Se usa esp_timer (microsegundos) y no
 * vTaskDelay: el tick de FreeRTOS no alcanza para 200-500 Hz. */
static void IRAM_ATTR tic_lazo(void *arg)
{
    (void)arg;
    BaseType_t despertar = pdFALSE;
    vTaskNotifyGiveFromISR(s_tarea_control, &despertar);
    portYIELD_FROM_ISR(despertar);
}

/* ------------------------------------------------------------ lazo de control */
static void tarea_control(void *arg)
{
    (void)arg;
    int64_t t_ant = esp_timer_get_time();
    int div_vesc = 0;

    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        const int64_t ahora = esp_timer_get_time();
        const float dt = (float)(ahora - t_ant) * 1e-6f;
        t_ant = ahora;

        /* 1) sensor */
        mpu_muestra_t m;
        if (mpu_leer(&m) != ESP_OK) {
            /* Sin IMU no hay control posible: desarma y corta torque. */
            bal_desarmar();
            vesc_stop();
            continue;
        }

        /* 2) realimentacion de velocidad de la VESC (mas lenta que el lazo) */
        vesc_poll_rx();
        if (++div_vesc >= (LOOP_HZ / VESC_POLL_HZ)) {
            div_vesc = 0;
            vesc_pedir_valores();
        }

        /* 3) estimacion + ley de control */
        bal_paso(&m, vesc_erpm(), dt);

        /* 4) actuacion */
        const bal_estado_t *e = bal_get();
        vesc_set_current(e->i_izq, e->i_der);

        /* 5) telemetria y captura */
        link_tick((float)ahora * 1e-6f);
    }
}

/* --------------------------------------------------- tarea lenta: comandos */
static void tarea_comandos(void *arg)
{
    (void)arg;
    for (;;) {
        link_poll_comandos();
        link_volcar_captura();
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

/* --------------------------------------------------------------------- main */
void app_main(void)
{
    link_init();
    link_send("\n# ================================================\n");
    link_send("# Robot balancin - firmware de balanceo\n");

    vesc_init();
    vesc_stop();

    if (mpu_init() != ESP_OK) {
        /* Se queda aqui a proposito: sin IMU no se arma nada. */
        for (;;) {
            link_send("# ERROR: MPU6050 no responde en 0x68 (revisa I2C/GND)\n");
            vTaskDelay(pdMS_TO_TICKS(2000));
        }
    }

    link_send("# MPU6050 OK. Calibrando bias del giroscopio: NO MOVER...\n");
    if (mpu_calibrar_bias(300) != ESP_OK)
        link_send("# AVISO: calibracion de bias incompleta\n");

    bal_init();

    char msg[96];
    snprintf(msg, sizeof(msg), "# Listo. Lazo a %d Hz, telemetria %d Hz.\n",
             LOOP_HZ, TELEMETRY_HZ);
    link_send(msg);
    link_send("# DESARMADO. Comandos: START STOP TELE_ON EQ GET K I CAPTURA\n");
    link_send("# ================================================\n");

    xTaskCreatePinnedToCore(tarea_control, "control", 4096, NULL,
                            configMAX_PRIORITIES - 2, &s_tarea_control, 1);
    xTaskCreatePinnedToCore(tarea_comandos, "comandos", 4096, NULL, 4, NULL, 0);

    const esp_timer_create_args_t targs = {
        .callback = &tic_lazo,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "lazo",
    };
    esp_timer_handle_t th;
    ESP_ERROR_CHECK(esp_timer_create(&targs, &th));
    ESP_ERROR_CHECK(esp_timer_start_periodic(th, 1000000ULL / LOOP_HZ));
}
