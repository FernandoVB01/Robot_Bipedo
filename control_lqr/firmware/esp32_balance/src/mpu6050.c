/* =============================================================================
 * MPU6050 por I2C  (ESP-IDF, C puro)
 * Basado en el firmware de prueba que ya funciona en el banco.
 * ========================================================================== */
#include "mpu6050.h"
#include "config.h"

#include "driver/i2c.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define I2C_PORT            I2C_NUM_0

/* Sensibilidades correspondientes a la configuracion de mpu_init(). */
#define ACC_LSB_PER_G       4096.0f      /* +-8 g */
#define GYRO_LSB_PER_DPS    65.5f        /* +-500 grados/s */
#define GRAVITY_M_S2        9.80665f
#define DEG_TO_RAD_F        0.017453292519943295f

static float s_bias_gx, s_bias_gy, s_bias_gz;

static esp_err_t reg_write(uint8_t reg, uint8_t valor)
{
    uint8_t b[2] = {reg, valor};
    return i2c_master_write_to_device(I2C_PORT, MPU_ADDR, b, sizeof(b),
                                      pdMS_TO_TICKS(100));
}

static esp_err_t reg_read(uint8_t reg, uint8_t *dst, size_t n)
{
    return i2c_master_write_read_device(I2C_PORT, MPU_ADDR, &reg, 1, dst, n,
                                        pdMS_TO_TICKS(100));
}

static int16_t junta(uint8_t hi, uint8_t lo)
{
    return (int16_t)(((uint16_t)hi << 8) | (uint16_t)lo);
}

esp_err_t mpu_init(void)
{
    i2c_config_t cfg = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = PIN_SDA,
        .scl_io_num = PIN_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_FREQ_HZ,
        .clk_flags = 0,
    };
    esp_err_t r = i2c_param_config(I2C_PORT, &cfg);
    if (r != ESP_OK) return r;
    r = i2c_driver_install(I2C_PORT, cfg.mode, 0, 0, 0);
    if (r != ESP_OK) return r;

    uint8_t quien = 0;
    r = reg_read(0x75, &quien, 1);
    if (r != ESP_OK) return r;
    if (quien != 0x68) return ESP_ERR_NOT_FOUND;

    if ((r = reg_write(0x6B, 0x01)) != ESP_OK) return r;   /* despertar, PLL giro X */
    vTaskDelay(pdMS_TO_TICKS(100));
    if ((r = reg_write(0x1A, 0x03)) != ESP_OK) return r;   /* DLPF ~44 Hz */

    /* Con DLPF activo la base es 1 kHz: divisor = 1000/LOOP_HZ - 1 */
    uint8_t div = (uint8_t)((1000 / LOOP_HZ) - 1);
    if ((r = reg_write(0x19, div)) != ESP_OK) return r;

    if ((r = reg_write(0x1B, 0x08)) != ESP_OK) return r;   /* giro +-500 dps */
    if ((r = reg_write(0x1C, 0x10)) != ESP_OK) return r;   /* acel +-8 g */
    return ESP_OK;
}

esp_err_t mpu_leer(mpu_muestra_t *out)
{
    uint8_t raw[14];
    esp_err_t r = reg_read(0x3B, raw, sizeof(raw));
    if (r != ESP_OK) return r;

    out->ax = junta(raw[0], raw[1]) * GRAVITY_M_S2 / ACC_LSB_PER_G;
    out->ay = junta(raw[2], raw[3]) * GRAVITY_M_S2 / ACC_LSB_PER_G;
    out->az = junta(raw[4], raw[5]) * GRAVITY_M_S2 / ACC_LSB_PER_G;
    /* raw[6..7] es la temperatura */
    out->gx = junta(raw[8], raw[9])   * DEG_TO_RAD_F / GYRO_LSB_PER_DPS - s_bias_gx;
    out->gy = junta(raw[10], raw[11]) * DEG_TO_RAD_F / GYRO_LSB_PER_DPS - s_bias_gy;
    out->gz = junta(raw[12], raw[13]) * DEG_TO_RAD_F / GYRO_LSB_PER_DPS - s_bias_gz;
    return ESP_OK;
}

esp_err_t mpu_calibrar_bias(int n)
{
    s_bias_gx = s_bias_gy = s_bias_gz = 0.0f;
    float sx = 0, sy = 0, sz = 0;
    int validas = 0;

    for (int i = 0; i < n; ++i) {
        mpu_muestra_t m;
        if (mpu_leer(&m) == ESP_OK) {
            sx += m.gx; sy += m.gy; sz += m.gz;
            ++validas;
        }
        vTaskDelay(pdMS_TO_TICKS(3));
    }
    if (validas < n / 2) return ESP_FAIL;

    s_bias_gx = sx / validas;
    s_bias_gy = sy / validas;
    s_bias_gz = sz / validas;
    return ESP_OK;
}
