function cfg = config_imu()
%CONFIG_IMU Ajustes de la prueba de IMU por puerto serie.
%
% Cambie solamente COM y BaudRate antes de conectar la ESP32.

cfg.COM = "COM5";
cfg.BaudRate = 115200;
cfg.Duracion_s = 30;
cfg.FrecuenciaEsperada_Hz = 100;
cfg.Timeout_s = 2;
cfg.CarpetaResultados = fullfile(fileparts(mfilename('fullpath')), "resultados");
cfg.PrefijoArchivo = "prueba_imu";
cfg.GraficarGyro = true;
end
