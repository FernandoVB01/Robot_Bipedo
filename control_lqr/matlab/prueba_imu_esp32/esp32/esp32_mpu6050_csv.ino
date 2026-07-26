/*
  Ejemplo ESP32 + MPU6050 para prueba colgada.
  Dependencias del Library Manager:
    Adafruit MPU6050, Adafruit Unified Sensor
  Salida: tiempo_ms,ax,ay,az,gx,gy,gz
  Unidades: m/s^2 y rad/s. Frecuencia aproximada: 100 Hz.
*/
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

Adafruit_MPU6050 mpu;
constexpr uint32_t BAUD = 115200;
constexpr uint32_t PERIODO_US = 10000;
uint32_t siguiente = 0;

void setup() {
  Serial.begin(BAUD);
  Wire.begin();
  if (!mpu.begin()) {
    while (true) {
      Serial.println("# ERROR: MPU6050 no detectado");
      delay(1000);
    }
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_44_HZ);
  siguiente = micros();
}

void loop() {
  const uint32_t ahora = micros();
  if ((int32_t)(ahora - siguiente) < 0) return;
  siguiente += PERIODO_US;

  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);
  Serial.printf("%lu,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n",
                millis(),
                a.acceleration.x, a.acceleration.y, a.acceleration.z,
                g.gyro.x, g.gyro.y, g.gyro.z);
}
