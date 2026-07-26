%% =====================================================================
%  FASE 3: Trayectoria del ocho por odometria (100 Hz) + analisis caida
%  Lee: mision_odom.csv (t vL vR yaw pitch) y mision_fases.txt
%% =====================================================================
clear; clc;

D = readmatrix('mision_odom.csv', 'FileType','text');
D = D(all(~isnan(D),2), :);
t = D(:,1); vL = D(:,2); vR = D(:,3); yaw = D(:,4); pitch = D(:,5);
Rw = 0.05;  tr = t - t(1);

% --- Fases desde el log ------------------------------------------------
txt = fileread('mision_fases.txt');
tok = regexp(txt, '\[(\d+\.\d+)\].*?->\s*([^\n\r]+)', 'tokens');
ph_t = cellfun(@(c) str2double(c{1}), tok) - t(1);
ph_n = cellfun(@(c) strtrim(c{2}), tok, 'UniformOutput', false);

% --- Deteccion de caida ------------------------------------------------
i_c = find(abs(pitch) > 0.6, 1);
n = size(D,1);
if ~isempty(i_c)
    fprintf('CAIDA en t = %.2f s\n', tr(i_c));
    n = i_c;
else
    fprintf('Sin caida registrada\n');
end

% --- Odometria (valida hasta la caida) ---------------------------------
v = Rw * (vL + vR) / 2;
x = zeros(n,1); y = zeros(n,1);
for k = 2:n
    dt = min(max(t(k)-t(k-1), 0), 0.05);
    x(k) = x(k-1) + v(k)*cos(yaw(k))*dt;
    y(k) = y(k-1) + v(k)*sin(yaw(k))*dt;
end

fig = figure('Visible','off', 'Position',[40 40 1250 540]);

subplot(1,2,1);
scatter(x, y, 10, tr(1:n), 'filled'); hold on;
plot(x(1), y(1), 'ks', 'MarkerFaceColor','g', 'MarkerSize', 10);
text(x(1), y(1), '  inicio');
if ~isempty(i_c)
    plot(x(end), y(end), 'rx', 'MarkerSize', 15, 'LineWidth', 3);
    text(x(end), y(end), '  CAIDA', 'Color','r', 'FontWeight','bold');
end
for k = 1:numel(ph_t)
    [~, idx] = min(abs(tr(1:n) - ph_t(k)));
    if abs(tr(idx) - ph_t(k)) < 0.5
        plot(x(idx), y(idx), 'ko', 'MarkerFaceColor','w', 'MarkerSize', 6);
        text(x(idx), y(idx), ['  ' ph_n{k}], 'FontSize', 8);
    end
end
axis equal; grid on; colorbar;
xlabel('x [m]'); ylabel('y [m]');
title('Trayectoria por odometria (color = t [s])');

subplot(1,2,2);
plot(tr, rad2deg(pitch), 'LineWidth', 1.1); hold on;
yline(rad2deg(-0.132552), '--', '\theta_{eq}');
for k = 1:numel(ph_t)
    xline(ph_t(k), ':', ph_n{k}, 'FontSize', 7, ...
          'LabelVerticalAlignment','bottom');
end
if ~isempty(i_c), xline(tr(i_c), 'r-', 'CAIDA', 'LineWidth', 1.5); end
ylim([-95 30]); grid on;
xlabel('t [s]'); ylabel('pitch [deg]');
title('Pitch a 100 Hz durante la mision');

saveas(fig, 'mision_ocho.png');
fprintf('Guardado: mision_ocho.png\n');
if ~isempty(i_c)
    iw = max(1, i_c-300):i_c;   % ultimos 3 s antes de la caida
    fprintf('Pitch 3s antes de caer: media %.1f deg | std %.1f deg\n', ...
        mean(rad2deg(pitch(iw))), std(rad2deg(pitch(iw))));
end
