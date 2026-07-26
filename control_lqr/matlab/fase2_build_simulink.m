%% =====================================================================
%  FASE 2b: Construye el modelo Simulink del lazo cerrado, lo simula
%  con perturbacion escalon y exporta las graficas a PNG.
%  Genera: fase2_lazo_cerrado.slx  +  respuesta_perturbacion.png
%  Requiere: lqr_design.mat (generado por fase2_lqr.m)
%% =====================================================================
clear; clc;  load('lqr_design.mat');

if ~license('test','Simulink')
    error('No hay licencia de Simulink en esta instalacion.');
end

mdl = 'fase2_lazo_cerrado';
if bdIsLoaded(mdl), close_system(mdl, 0); end
if exist(fullfile(pwd, [mdl '.slx']), 'file')
    delete(fullfile(pwd, [mdl '.slx']));
end
new_system(mdl);

%% --- Bloques ----------------------------------------------------------
% Planta continua (el "robot real")
add_block('simulink/Continuous/State-Space', [mdl '/Planta'], ...
    'A','A', 'B','B', 'C','C', 'D','D', ...
    'X0','[0; 0; 0.05; 0]', ...              % ~2.9 deg de error inicial
    'Position', [340 92 460 148]);

% Perturbacion escalon en la entrada (torque externo de 0.3 N*m en t=1s)
add_block('simulink/Sources/Step', [mdl '/Perturbacion'], ...
    'Time','1', 'Before','0', 'After','0.3', ...
    'Position', [60 92 100 128]);

% Suma: u = d - Kd*x
add_block('simulink/Math Operations/Sum', [mdl '/Sum'], ...
    'Inputs','|+-', 'Position', [200 100 230 130]);

% Muestreo a 100 Hz del vector de estados
add_block('simulink/Discrete/Zero-Order Hold', [mdl '/ZOH'], ...
    'SampleTime','Ts', 'Orientation','left', ...
    'Position', [480 210 520 250]);

% Ganancia LQR discreta (entrada: vector 4x1, salida: escalar)
add_block('simulink/Math Operations/Gain', [mdl '/LQR'], ...
    'Gain','Kd', 'Multiplication','Matrix(K*u)', ...
    'Orientation','left', 'Position', [300 208 360 252]);

% Registro y visualizacion
add_block('simulink/Sinks/To Workspace', [mdl '/estados'], ...
    'VariableName','estados', 'SaveFormat','Timeseries', ...
    'Position', [560 60 620 90]);
add_block('simulink/Sinks/To Workspace', [mdl '/torque'], ...
    'VariableName','torque', 'SaveFormat','Timeseries', ...
    'Position', [300 20 360 50]);
add_block('simulink/Sinks/Scope', [mdl '/Scope Estados'], ...
    'Position', [560 110 600 150]);
add_block('simulink/Sinks/Scope', [mdl '/Scope Torque'], ...
    'Position', [300 350 340 390]);

%% --- Conexiones -------------------------------------------------------
add_line(mdl, 'Perturbacion/1', 'Sum/1',    'autorouting','on');
add_line(mdl, 'Sum/1',          'Planta/1', 'autorouting','on');
add_line(mdl, 'Planta/1',       'ZOH/1',    'autorouting','on');
add_line(mdl, 'ZOH/1',          'LQR/1',    'autorouting','on');
add_line(mdl, 'LQR/1',          'Sum/2',    'autorouting','on');
add_line(mdl, 'Planta/1',  'estados/1',       'autorouting','on');
add_line(mdl, 'Planta/1',  'Scope Estados/1', 'autorouting','on');
add_line(mdl, 'Sum/1',     'torque/1',        'autorouting','on');
add_line(mdl, 'Sum/1',     'Scope Torque/1',  'autorouting','on');

%% --- Configuracion y simulacion ---------------------------------------
set_param(mdl, 'StopTime','5', 'Solver','ode45');
save_system(mdl);
fprintf('Modelo guardado: %s.slx\n', fullfile(pwd, mdl));

out  = sim(mdl);
est  = out.estados;      % timeseries 4 estados
tauL = out.torque;       % timeseries torque

%% --- Graficas ---------------------------------------------------------
fig = figure('Visible','off', 'Position',[100 100 900 700]);

subplot(3,1,1);
plot(est.Time, est.Data(:,1), 'LineWidth', 1.5); grid on;
ylabel('x  [m]');
title('Lazo cerrado LQR 100 Hz - perturbacion escalon de 0.3 N*m en t = 1 s');

subplot(3,1,2);
plot(est.Time, rad2deg(est.Data(:,3)), 'LineWidth', 1.5); grid on;
ylabel('\delta\theta  [deg]');

subplot(3,1,3);
plot(tauL.Time, tauL.Data, 'LineWidth', 1.5); grid on;
ylabel('\tau  [N\cdotm]'); xlabel('t  [s]');

saveas(fig, 'respuesta_perturbacion.png');
fprintf('Grafica guardada: %s\n', fullfile(pwd,'respuesta_perturbacion.png'));

%% --- Resumen numerico -------------------------------------------------
fprintf('\n--- Resumen de la respuesta ---\n');
fprintf('Pico |delta_theta| : %.2f deg\n', max(abs(rad2deg(est.Data(:,3)))));
fprintf('Pico |torque|      : %.3f N*m (limite motor URDF: 2.0)\n', ...
        max(abs(tauL.Data)));
fprintf('x final (t=5s)     : %.4f m\n', est.Data(end,1));

close_system(mdl, 0);
