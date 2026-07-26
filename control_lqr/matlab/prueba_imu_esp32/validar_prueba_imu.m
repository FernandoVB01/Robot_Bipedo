%% Validación sin hardware del parser y del guardado
clear; clc;

casos = {
    "0,0.01,-0.02,9.81", 4
    "10,0.02,-0.01,9.80,0.001,0.002,-0.003", 7
    "20;0.03;-0.02;9.79;0.004;0.005;0.006", 7
    "cabecera,ax,ay,az", 0
    "1,2,3", 0
    };

for k = 1:size(casos,1)
    [m, ok] = parsear_trama_imu(casos{k,1});
    esperado = casos{k,2};
    assert(ok == (esperado > 0));
    if esperado == 4
        assert(all(isfinite(m(1:4))) && all(isnan(m(5:7))));
    elseif esperado == 7
        assert(all(isfinite(m)));
    end
end

fprintf('Validación del parser IMU superada (%d casos).\n', size(casos,1));
