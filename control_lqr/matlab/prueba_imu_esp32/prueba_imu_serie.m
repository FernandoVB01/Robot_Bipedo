function resultado = prueba_imu_serie(cfg)
%PRUEBA_IMU_SERIE Adquiere, grafica y guarda la IMU de una ESP32.
%
% Uso:
%   cfg = config_imu;
%   cfg.COM = "COM5";
%   resultado = prueba_imu_serie(cfg);
%
% La ESP32 debe enviar una línea CSV por muestra:
%   tiempo_ms,ax,ay,az
% o:
%   tiempo_ms,ax,ay,az,gx,gy,gz
% Aceleración en m/s^2 y giroscopio en rad/s.

if nargin < 1
    cfg = config_imu();
end
validarConfiguracion(cfg);

if ~exist(cfg.CarpetaResultados, 'dir')
    mkdir(cfg.CarpetaResultados);
end

puertos = string(serialportlist("available"));
if ~any(strcmpi(puertos, string(cfg.COM)))
    error('IMU:PuertoNoDisponible', ...
        'El puerto %s no está disponible. Detectados: %s', ...
        cfg.COM, strjoin(puertos, ', '));
end

sp = serialport(cfg.COM, cfg.BaudRate, "Timeout", cfg.Timeout_s);
limpieza = onCleanup(@() limpiarPuerto(sp));
configureTerminator(sp, "LF");
flush(sp);
pause(1.0); % muchas ESP32 se reinician al abrir el puerto
flush(sp);

nEstimado = max(100, ceil(cfg.Duracion_s * cfg.FrecuenciaEsperada_Hz * 1.2));
datos = nan(nEstimado, 7);
n = 0;
descartadas = 0;

fig = figure('Name', 'Prueba IMU ESP32', 'NumberTitle', 'off', ...
    'Color', 'w', 'Position', [100 80 1000 720]);
ax1 = subplot(2,1,1, 'Parent', fig);
la = [animatedline(ax1,'DisplayName','a_x'), ...
      animatedline(ax1,'DisplayName','a_y'), ...
      animatedline(ax1,'DisplayName','a_z')];
grid(ax1,'on'); ylabel(ax1,'Aceleración [m/s^2]');
title(ax1, sprintf('IMU por %s @ %d baud', cfg.COM, cfg.BaudRate));
legend(ax1,'Location','eastoutside');

ax2 = subplot(2,1,2, 'Parent', fig);
lg = [animatedline(ax2,'DisplayName','g_x'), ...
      animatedline(ax2,'DisplayName','g_y'), ...
      animatedline(ax2,'DisplayName','g_z')];
grid(ax2,'on'); ylabel(ax2,'Velocidad angular [rad/s]');
xlabel(ax2,'Tiempo [s]'); legend(ax2,'Location','eastoutside');

fprintf('Adquiriendo durante %.1f s. Para antes cerrando la figura.\n', cfg.Duracion_s);
tInicio = tic;
while toc(tInicio) < cfg.Duracion_s && isgraphics(fig)
    if sp.NumBytesAvailable == 0
        pause(0.001);
        continue;
    end
    linea = readline(sp);
    [muestra, valida] = parsear_trama_imu(linea);
    if ~valida
        descartadas = descartadas + 1;
        continue;
    end
    n = n + 1;
    if n > size(datos,1)
        datos = [datos; nan(nEstimado,7)]; %#ok<AGROW>
    end
    datos(n,:) = muestra;
    t = muestra(1)/1000;
    for k = 1:3
        addpoints(la(k), t, muestra(k+1));
        if cfg.GraficarGyro && all(isfinite(muestra(5:7)))
            addpoints(lg(k), t, muestra(k+4));
        end
    end
    if mod(n,5) == 0
        drawnow limitrate;
    end
end

if n == 0
    error('IMU:SinDatos', ['No se recibió ninguna trama válida. Revise COM, baud ' ...
        'y el formato CSV del firmware.']);
end

datos = datos(1:n,:);
tiempo_s = (datos(:,1) - datos(1,1))/1000;
tabla = table(tiempo_s, datos(:,1), datos(:,2), datos(:,3), datos(:,4), ...
    datos(:,5), datos(:,6), datos(:,7), ...
    'VariableNames', {'tiempo_s','esp32_ms','ax','ay','az','gx','gy','gz'});

sello = datestr(now, 'yyyymmdd_HHMMSS');
base = fullfile(cfg.CarpetaResultados, sprintf('%s_%s', cfg.PrefijoArchivo, sello));
csvFile = [base '.csv'];
matFile = [base '.mat'];
pngFile = [base '.png'];
writetable(tabla, csvFile);
save(matFile, 'tabla', 'cfg', 'descartadas');
if isgraphics(fig)
    saveas(fig, pngFile);
end

dt = diff(tiempo_s);
hz = NaN;
if ~isempty(dt) && median(dt) > 0
    hz = 1/median(dt);
end
fprintf('Listo: %d muestras, %d descartadas, frecuencia mediana %.1f Hz.\n', ...
    n, descartadas, hz);
fprintf('Guardado en:\n  %s\n  %s\n  %s\n', csvFile, matFile, pngFile);

resultado = struct('tabla', tabla, 'csv', csvFile, 'mat', matFile, ...
    'png', pngFile, 'frecuencia_Hz', hz, 'descartadas', descartadas);
end

function validarConfiguracion(cfg)
requeridos = {'COM','BaudRate','Duracion_s','FrecuenciaEsperada_Hz', ...
    'Timeout_s','CarpetaResultados','PrefijoArchivo','GraficarGyro'};
for k = 1:numel(requeridos)
    if ~isfield(cfg, requeridos{k})
        error('IMU:Configuracion', 'Falta cfg.%s.', requeridos{k});
    end
end
end

function limpiarPuerto(sp)
if ~isempty(sp) && isvalid(sp)
    flush(sp);
end
end
