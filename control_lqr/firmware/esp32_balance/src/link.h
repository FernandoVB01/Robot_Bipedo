#pragma once
/* Enlace ASCII con la Raspberry (UART1) y con la PC de banco (UART0/USB):
 * comandos entrantes, telemetria saliente y captura en rafaga. */
#include <stdbool.h>

void link_init(void);

/** Escribe una linea en AMBOS puertos (Pi y USB). */
void link_send(const char *linea);

/** Lee y despacha los comandos pendientes. Llamar desde una tarea aparte,
 *  nunca desde el lazo de control. */
void link_poll_comandos(void);

/** Llamar una vez por vuelta del lazo: publica telemetria diezmada y
 *  alimenta el buffer de captura. */
void link_tick(float t_s);

/** Vuelca por el enlace la captura en rafaga ya terminada (tarea lenta). */
void link_volcar_captura(void);
