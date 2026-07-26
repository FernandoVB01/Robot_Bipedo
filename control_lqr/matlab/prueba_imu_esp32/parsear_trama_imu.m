function [muestra, valida] = parsear_trama_imu(linea)
%PARSEAR_TRAMA_IMU Convierte una línea CSV de la ESP32.
% Formatos aceptados:
%   tiempo_ms,ax,ay,az
%   tiempo_ms,ax,ay,az,gx,gy,gz

muestra = nan(1,7);
valida = false;

if isstring(linea) || ischar(linea)
    valores = sscanf(strrep(char(linea), ';', ','), '%f,%f,%f,%f,%f,%f,%f');
else
    return;
end

if numel(valores) == 4
    muestra(1:4) = valores(:).';
    valida = all(isfinite(muestra(1:4)));
elseif numel(valores) == 7
    muestra = valores(:).';
    valida = all(isfinite(muestra));
end
end
