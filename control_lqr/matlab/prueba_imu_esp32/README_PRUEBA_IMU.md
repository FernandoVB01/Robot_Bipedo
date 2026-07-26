# Prueba de IMU con ESP32 y el robot colgado

Esta carpeta es independiente del diseño LQR existente. No modifica los modelos
ni los archivos de las fases 1–4.

## 1. Preparar la ESP32

La ESP32 debe emitir una muestra CSV por línea, sin texto adicional:

```text
tiempo_ms,ax,ay,az
tiempo_ms,ax,ay,az,gx,gy,gz
```

Use `m/s^2` para aceleración y `rad/s` para giroscopio. Se incluye
`esp32/esp32_mpu6050_csv.ino` como ejemplo para un MPU6050 a 100 Hz. Si el
sensor es otro, conserve el formato de salida.

## 2. Configurar MATLAB

Abra MATLAB en esta carpeta y ejecute:

```matlab
serialportlist("available")
edit config_imu
```

Cambie `cfg.COM` (por ejemplo, `"COM5"`) y confirme `cfg.BaudRate`. El baud
de MATLAB y el de la ESP32 deben ser iguales. Cierre el monitor serie de
Arduino antes de iniciar MATLAB: un puerto COM normalmente solo puede tener
un usuario.

## 3. Ejecutar

Con el robot apagado y colgado de forma segura:

```matlab
cfg = config_imu;
cfg.Duracion_s = 30;
r = prueba_imu_serie(cfg);
```

Se muestran dos gráficas en vivo: `ax, ay, az` y, si están presentes,
`gx, gy, gz`. Al terminar se crean en `resultados/`:

- CSV para análisis o Excel.
- MAT con la tabla, configuración y conteo de tramas descartadas.
- PNG de las gráficas.

Para detener antes de tiempo, cierre la ventana de la gráfica.

## 4. Comprobaciones rápidas

- Quieto: la magnitud `sqrt(ax^2+ay^2+az^2)` debe estar cerca de `9.81 m/s^2`.
- Al inclinar hacia adelante: anote qué eje cambia y el signo. Esto permite
  comprobar la convención de pitch usada por el controlador.
- Quieto: el giroscopio debe estar cerca de cero; un desplazamiento constante
  indica que conviene calibrar bias antes de cerrar el LQR.
- La frecuencia reportada debería estar cerca de 100 Hz, igual al período de
  control actual (`Ts = 0.01 s`).

## 5. Validación sin conectar hardware

```matlab
validar_prueba_imu
```

Esto prueba el parser con tramas de acelerómetro, tramas con giroscopio,
separador alternativo y entradas inválidas.

## Seguridad

Esta prueba solo mide sensores: no envía torque a los motores. Mantenga los
motores deshabilitados, sujete el robot con dos puntos independientes y deje
las ruedas libres. No cierre el lazo LQR hasta confirmar ejes, signos,
unidades, frecuencia, ruido y bias.
