%% =====================================================================
%  Analisis del instante del reset: por que el LQR no atrapa al robot
%  catch.csv: t vL vR yaw pitch (100 Hz) | cmd.csv: torques | reset_epoch
%% =====================================================================
clear; clc;

C = readmatrix('catch.csv', 'FileType','text');
C = C(all(~isnan(C),2), :);
t_reset = str2double(strtrim(fileread('reset_epoch.txt')));
t  = C(:,1) - t_reset;
vL = C(:,2); vR = C(:,3); yaw = C(:,4); pitch = C(:,5);

% Comandos: extrae los dos ultimos numeros de cada linea
raw = strsplit(strtrim(fileread('cmd.csv')), '\n');
tau = nan(numel(raw), 2);
for k = 1:numel(raw)
    p = strsplit(raw{k}, ',');
    if numel(p) >= 2
        a = str2double(p{end-1}); b = str2double(p{end});
        if ~isnan(a) && ~isnan(b), tau(k,:) = [a b]; end
    end
end
tau = tau(all(~isnan(tau),2), :);
% eje temporal aprox de comandos: 100 Hz, echo arranco ~2 s antes del reset
tc = (0:size(tau,1)-1).'/100 - 2.0;

fig = figure('Visible','off', 'Position',[40 40 1100 720]);
subplot(3,1,1);
plot(t, rad2deg(pitch), 'LineWidth', 1.2); hold on;
yline(rad2deg(-0.132552), '--', '\theta_{eq}');
xline(0, 'r-', 'RESET');
xlim([-1 4]); grid on; ylabel('pitch [deg]');
title('Instante del reset: pitch, velocidad de rueda y torque');

subplot(3,1,2);
plot(t, vL, t, vR, 'LineWidth', 1.1); hold on; xline(0, 'r-');
xlim([-1 4]); grid on; ylabel('\omega rueda [rad/s]');
legend('izq','der', 'Location','best');

subplot(3,1,3);
plot(tc, tau(:,1), tc, tau(:,2), 'LineWidth', 1.1); hold on; xline(0, 'r-');
xlim([-1 4]); grid on; ylabel('\tau [N\cdotm]'); xlabel('t desde reset [s]');
legend('izq','der', 'Location','best');

saveas(fig, 'analisis_catch.png');
fprintf('Guardado: analisis_catch.png\n\n');

% Tabla numerica del primer segundo
fprintf('   t[s]   pitch[deg]   wL[rad/s]   wR[rad/s]\n');
for tq = -0.1:0.1:1.2
    [~, i] = min(abs(t - tq));
    fprintf('%7.2f   %9.2f   %9.2f   %9.2f\n', ...
            t(i), rad2deg(pitch(i)), vL(i), vR(i));
end
i1 = tc > 0 & tc < 1;
fprintf('\nTorque max |izq| %.3f | |der| %.3f en el 1er segundo\n', ...
        max(abs(tau(i1,1))), max(abs(tau(i1,2))));
