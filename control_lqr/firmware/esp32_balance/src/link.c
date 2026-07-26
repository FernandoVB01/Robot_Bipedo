/* =============================================================================
 * ENLACE ASCII: comandos + telemetria
 * -----------------------------------------------------------------------------
 * Los mismos comandos se aceptan por el UART de la Pi y por el USB, asi se
 * puede probar todo en el banco con la PC antes de conectar la Raspberry.
 *
 * Telemetria (APAGADA al arrancar, se enciende con TELE_ON):
 *     T,tiempo_ms,pitch_deg,gyro_rads,u_nm,i_a,erpm,estado
 *
 * Se publica diezmada a TELEMETRY_HZ: a 115200 baud una linea de ~48 bytes a
 * 50 Hz ocupa ~21% del enlace; a 500 Hz no entraria.
 * ========================================================================== */
#include "link.h"
#include "config.h"
#include "balance.h"
#include "vesc.h"

#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"

#define LINEA_MAX   96

static bool s_tele;
static int  s_div;                       /* contador de diezmado */

/* --- captura en rafaga --------------------------------------------------- */
typedef struct { float t, pitch, gyro, u; } captura_t;
static captura_t s_cap[CAPTURA_MUESTRAS];
static int  s_cap_n;
static bool s_cap_activa;

/* --- ensamblado de lineas por puerto ------------------------------------- */
typedef struct { char buf[LINEA_MAX]; int n; } lector_t;
static lector_t s_lec[2];
static const uart_port_t s_puertos[2] = {UART_NUM_0, PI_UART_NUM};

/* ----------------------------------------------------------------- utilidad */
void link_send(const char *linea)
{
    const int n = (int)strlen(linea);
    for (int i = 0; i < 2; ++i)
        uart_write_bytes(s_puertos[i], linea, n);
}

static void responder(const char *fmt, ...)
{
    char tmp[LINEA_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    link_send(tmp);
}

/* ------------------------------------------------------------- despachador */
static void ejecutar(char *c)
{
    /* quita espacios finales y retorno de carro */
    int n = (int)strlen(c);
    while (n > 0 && (c[n - 1] == '\r' || c[n - 1] == ' ' || c[n - 1] == '\t'))
        c[--n] = '\0';
    if (n == 0) return;

    float a, b, cc, d;

    if (strcmp(c, "PING") == 0) {
        responder("ACK:PING\n");

    } else if (strcmp(c, "START") == 0) {
        if (bal_armar()) responder("ACK:START\n");
        else responder("ERR:FUERA_DE_VENTANA\n");

    } else if (strcmp(c, "STOP") == 0 || strcmp(c, "PARAR") == 0) {
        bal_desarmar();
        vesc_stop();
        responder("ACK:%s\n", c);

    } else if (strcmp(c, "TELE_ON") == 0) {
        s_tele = true;  responder("ACK:TELE_ON\n");

    } else if (strcmp(c, "TELE_OFF") == 0) {
        s_tele = false; responder("ACK:TELE_OFF\n");

    } else if (strcmp(c, "EQ") == 0) {
        bal_fijar_equilibrio();
        responder("ACK:EQ %.4f rad\n", bal_equilibrio());

    } else if (strcmp(c, "CAPTURA") == 0) {
        s_cap_n = 0; s_cap_activa = true;
        responder("ACK:CAPTURA\n");

    } else if (strcmp(c, "GET") == 0) {
        float k[4]; bal_get_ganancias(k);
        const bal_estado_t *e = bal_get();
        responder("ACK:GET k=%.3f,%.3f,%.3f,%.3f eq=%.4f estado=%s\n",
                  k[0], k[1], k[2], k[3], bal_equilibrio(), bal_estado_str());
        responder("# pitch=%.2f deg  x=%.3f m  dx=%.3f m/s\n",
                  e->pitch * 57.2957795f, e->x, e->dx);

    } else if (sscanf(c, "K %f %f %f %f", &a, &b, &cc, &d) == 4) {
        bal_set_ganancias(a, b, cc, d);
        responder("ACK:K %.3f %.3f %.3f %.3f\n", a, b, cc, d);

    } else if (sscanf(c, "I %f", &a) == 1) {
        /* Etapa 1: corriente fija de prueba, solo desarmado y con ruedas
         * en el aire. Es el unico camino que mueve motores sin lazo. */
        bal_test_corriente(a);
        responder("ACK:I %.2f\n", a);

    } else if (strcmp(c, "VDUMP") == 0) {
        char tmp[LINEA_MAX * 3];
        vesc_dump_ultima_respuesta(tmp, sizeof(tmp));
        link_send(tmp);

    /* --- comandos de navegacion del protocolo que ya usa la Pi ---------- */
    } else if (strcmp(c, "AVANZAR") == 0) {
        bal_set_referencia(0.20f, 0.0f);   responder("ACK:AVANZAR\n");
    } else if (strcmp(c, "ACERCAR") == 0) {
        bal_set_referencia(0.10f, 0.0f);   responder("ACK:ACERCAR\n");
    } else if (strcmp(c, "RETROCEDER") == 0) {
        bal_set_referencia(-0.15f, 0.0f);  responder("ACK:RETROCEDER\n");
    } else if (strcmp(c, "GIRAR_180") == 0) {
        bal_set_referencia(0.0f, 0.6f);    responder("ACK:GIRAR_180\n");

    } else {
        responder("ERR:DESCONOCIDO\n");
    }
}

/* ------------------------------------------------------------------- lectura */
void link_poll_comandos(void)
{
    for (int p = 0; p < 2; ++p) {
        uint8_t b;
        while (uart_read_bytes(s_puertos[p], &b, 1, 0) == 1) {
            lector_t *L = &s_lec[p];
            if (b == '\n') {
                L->buf[L->n] = '\0';
                ejecutar(L->buf);
                L->n = 0;
            } else if (L->n < LINEA_MAX - 1) {
                L->buf[L->n++] = (char)b;
            } else {
                L->n = 0;                  /* linea absurda: descarta */
            }
        }
    }
}

/* --------------------------------------------------------------- telemetria */
void link_tick(float t_s)
{
    const bal_estado_t *e = bal_get();

    if (s_cap_activa) {
        if (s_cap_n < CAPTURA_MUESTRAS) {
            s_cap[s_cap_n].t     = t_s;
            s_cap[s_cap_n].pitch = e->pitch;
            s_cap[s_cap_n].gyro  = e->pitch_rate;
            s_cap[s_cap_n].u     = e->u_nm;
            ++s_cap_n;
        } else {
            s_cap_activa = false;          /* el volcado lo hace la tarea lenta */
        }
    }

    if (!s_tele) return;
    if (++s_div < (LOOP_HZ / TELEMETRY_HZ)) return;
    s_div = 0;

    char tmp[LINEA_MAX];
    snprintf(tmp, sizeof(tmp), "T,%lu,%.2f,%.4f,%.4f,%.3f,%.0f,%s\n",
             (unsigned long)(t_s * 1000.0f),
             e->pitch * 57.2957795f, e->pitch_rate, e->u_nm,
             e->i_izq, vesc_erpm(), bal_estado_str());
    link_send(tmp);
}

/** Volcado de la captura: lo llama la tarea de comandos cuando termino. */
void link_volcar_captura(void)
{
    if (s_cap_activa || s_cap_n == 0) return;

    /* Silencia la telemetria mientras dura el volcado: si no, las lineas T
     * del lazo se entremezclan con las de la captura. */
    const bool tele_previa = s_tele;
    s_tele = false;

    char tmp[LINEA_MAX];
    link_send("# CAPTURA inicio: t_s,pitch_rad,gyro_rads,u_nm\n");
    for (int i = 0; i < s_cap_n; ++i) {
        snprintf(tmp, sizeof(tmp), "# %.4f,%.5f,%.5f,%.4f\n",
                 s_cap[i].t, s_cap[i].pitch, s_cap[i].gyro, s_cap[i].u);
        link_send(tmp);
        vTaskDelay(1);                     /* no saturar el UART */
    }
    link_send("# CAPTURA fin\n");
    s_cap_n = 0;
    s_tele = tele_previa;
}

/* ------------------------------------------------------------------- init */
void link_init(void)
{
    uart_config_t c = {
        .baud_rate = PI_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };

    /* UART0: consola USB (banco). El driver reemplaza a printf: a partir de
     * aqui toda la salida va por link_send(). */
    uart_driver_install(UART_NUM_0, 1024, 1024, 0, NULL, 0);
    uart_param_config(UART_NUM_0, &c);

    /* UART1: Raspberry Pi */
    uart_driver_install(PI_UART_NUM, 1024, 1024, 0, NULL, 0);
    uart_param_config(PI_UART_NUM, &c);
    uart_set_pin(PI_UART_NUM, PI_UART_TX_PIN, PI_UART_RX_PIN,
                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
}
