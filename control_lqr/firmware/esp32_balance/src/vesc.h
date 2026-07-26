#pragma once
/* Protocolo UART de la VESC: enviar corriente y leer ERPM. */
#include <stdbool.h>

/** Instala el driver del UART2 hacia la VESC. */
void vesc_init(void);

/** Fija la corriente [A] de cada rueda.
 *  La maestra recibe por UART; la esclava por CAN (COMM_FORWARD_CAN). */
void vesc_set_current(float izq_a, float der_a);

/** Corriente cero en ambas ruedas. */
void vesc_stop(void);

/** Pide COMM_GET_VALUES a la maestra (no bloquea; la respuesta se procesa
 *  en vesc_poll_rx). */
void vesc_pedir_valores(void);

/** Procesa lo que haya llegado de la VESC. Devuelve true si actualizo el ERPM. */
bool vesc_poll_rx(void);

/** Ultimo ERPM recibido de la maestra. */
float vesc_erpm(void);

/** Vuelca en hexadecimal la ultima respuesta cruda de GET_VALUES.
 *  Sirve para verificar el desplazamiento del campo rpm en TU firmware VESC. */
void vesc_dump_ultima_respuesta(char *dst, int max);
