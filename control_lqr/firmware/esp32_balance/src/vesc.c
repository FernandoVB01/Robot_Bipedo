/* =============================================================================
 * PROTOCOLO VESC POR UART
 * -----------------------------------------------------------------------------
 * Trama:  [0x02][len][payload...][CRC16_hi][CRC16_lo][0x03]
 * CRC16 CCITT/XMODEM (poly 0x1021) sobre el payload.
 *
 * Se usa COMM_SET_CURRENT (corriente = torque), NO COMM_SET_DUTY: en un
 * balancin la variable de control es el torque.
 * ========================================================================== */
#include "vesc.h"
#include "config.h"

#include <string.h>
#include <stdio.h>
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"

#define COMM_GET_VALUES     4
#define COMM_SET_CURRENT    6
#define COMM_FORWARD_CAN    34

/* -----------------------------------------------------------------------------
 * Desplazamiento del campo rpm dentro del payload de GET_VALUES.
 * Orden en VESC 5.x:  temp_fet(2) temp_motor(2) avg_motor_current(4)
 *                     avg_input_current(4) avg_id(4) avg_iq(4) duty(2) rpm(4)...
 * payload[0] es el ID del comando, por eso 1 + 22 = 23.
 * DEPENDE DE LA VERSION DEL FIRMWARE VESC: verificar con el comando "VDUMP"
 * antes de confiar en la realimentacion de velocidad.
 * -------------------------------------------------------------------------- */
#define RPM_OFFSET          23

static float s_erpm;
static uint8_t s_ultima[128];
static int s_ultima_len;

/* --------------------------------------------------------------------- CRC16 */
static uint16_t crc16(const uint8_t *buf, int len)
{
    uint16_t crc = 0;
    for (int i = 0; i < len; ++i) {
        crc ^= (uint16_t)buf[i] << 8;
        for (int b = 0; b < 8; ++b)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021)
                                 : (uint16_t)(crc << 1);
    }
    return crc;
}

static void enviar_payload(const uint8_t *payload, int len)
{
    uint8_t t[64];
    int i = 0;
    t[i++] = 0x02;
    t[i++] = (uint8_t)len;
    memcpy(&t[i], payload, len);
    i += len;
    uint16_t c = crc16(payload, len);
    t[i++] = (uint8_t)(c >> 8);
    t[i++] = (uint8_t)(c & 0xFF);
    t[i++] = 0x03;
    uart_write_bytes(VESC_UART_NUM, (const char *)t, i);
}

static int poner_i32(uint8_t *p, int i, int32_t v)
{
    p[i++] = (uint8_t)(v >> 24);
    p[i++] = (uint8_t)(v >> 16);
    p[i++] = (uint8_t)(v >> 8);
    p[i++] = (uint8_t)v;
    return i;
}

static int32_t leer_i32(const uint8_t *p)
{
    return ((int32_t)p[0] << 24) | ((int32_t)p[1] << 16) |
           ((int32_t)p[2] << 8)  |  (int32_t)p[3];
}

/* ----------------------------------------------------------------- API publica */
void vesc_init(void)
{
    uart_config_t c = {
        .baud_rate = VESC_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };
    uart_driver_install(VESC_UART_NUM, 512, 0, 0, NULL, 0);
    uart_param_config(VESC_UART_NUM, &c);
    uart_set_pin(VESC_UART_NUM, VESC_UART_TX_PIN, VESC_UART_RX_PIN,
                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
}

void vesc_set_current(float izq_a, float der_a)
{
    if (izq_a >  CURRENT_MAX_A) izq_a =  CURRENT_MAX_A;
    if (izq_a < -CURRENT_MAX_A) izq_a = -CURRENT_MAX_A;
    if (der_a >  CURRENT_MAX_A) der_a =  CURRENT_MAX_A;
    if (der_a < -CURRENT_MAX_A) der_a = -CURRENT_MAX_A;

    uint8_t p[16];
    int i;

    /* Rueda 1: la VESC maestra, directo por UART. */
    i = 0;
    p[i++] = COMM_SET_CURRENT;
    i = poner_i32(p, i, (int32_t)(izq_a * 1000.0f));
    enviar_payload(p, i);

    /* Rueda 2: la maestra lo reenvia por CAN a la esclava. */
    i = 0;
    p[i++] = COMM_FORWARD_CAN;
    p[i++] = VESC_CAN_ID_SLAVE;
    p[i++] = COMM_SET_CURRENT;
    i = poner_i32(p, i, (int32_t)(der_a * 1000.0f));
    enviar_payload(p, i);
}

void vesc_stop(void)
{
    vesc_set_current(0.0f, 0.0f);
}

void vesc_pedir_valores(void)
{
    uint8_t p[1] = {COMM_GET_VALUES};
    enviar_payload(p, 1);
}

bool vesc_poll_rx(void)
{
    uint8_t buf[256];
    int n = uart_read_bytes(VESC_UART_NUM, buf, sizeof(buf), 0);
    if (n <= 0) return false;

    /* Busca una trama corta bien formada: [0x02][len][payload][crc][0x03] */
    for (int i = 0; i + 2 < n; ++i) {
        if (buf[i] != 0x02) continue;
        int len = buf[i + 1];
        if (len <= 0 || i + 2 + len + 3 > n) continue;

        const uint8_t *payload = &buf[i + 2];
        uint16_t recibido = ((uint16_t)payload[len] << 8) | payload[len + 1];
        if (crc16(payload, len) != recibido) continue;
        if (payload[0] != COMM_GET_VALUES) continue;

        s_ultima_len = len < (int)sizeof(s_ultima) ? len : (int)sizeof(s_ultima);
        memcpy(s_ultima, payload, s_ultima_len);

        if (len > RPM_OFFSET + 3) {
            s_erpm = (float)leer_i32(&payload[RPM_OFFSET]);
            return true;
        }
    }
    return false;
}

float vesc_erpm(void)
{
    return s_erpm;
}

void vesc_dump_ultima_respuesta(char *dst, int max)
{
    int p = snprintf(dst, max, "# VDUMP len=%d:", s_ultima_len);
    for (int i = 0; i < s_ultima_len && p < max - 4; ++i)
        p += snprintf(dst + p, max - p, " %02X", s_ultima[i]);
    snprintf(dst + p, max - p, "\n");
}
