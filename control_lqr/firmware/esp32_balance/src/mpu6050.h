#pragma once
/* Lectura del MPU6050 por I2C. Unidades de salida: m/s^2 y rad/s. */
#include "esp_err.h"

typedef struct {
    float ax, ay, az;      /* aceleracion [m/s^2] */
    float gx, gy, gz;      /* velocidad angular [rad/s] */
} mpu_muestra_t;

/** Inicializa el bus I2C y el sensor. Devuelve ESP_ERR_NOT_FOUND si el
 *  WHO_AM_I no responde 0x68 (sensor ausente o mal cableado). */
esp_err_t mpu_init(void);

/** Lee una muestra completa (acelerometro + giroscopio). */
esp_err_t mpu_leer(mpu_muestra_t *out);

/** Promedia n muestras con el robot QUIETO y guarda el bias del giroscopio,
 *  que se resta en todas las lecturas siguientes. */
esp_err_t mpu_calibrar_bias(int n);
