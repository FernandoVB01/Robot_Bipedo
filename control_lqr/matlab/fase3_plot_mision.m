%% =====================================================================
%  FASE 3: Grafica la trayectoria real ejecutada en Gazebo
%  Lee mision_trayectoria.csv (epoch x y z R P Y) muestreado ~1 Hz
%% =====================================================================
clear; clc;

d = readmatrix('mision_trayectoria.csv', 'FileType','text');
d = d(all(~isnan(d(:,1:7)),2), 1:7);      % solo filas completas
t  = d(:,1);  x = d(:,2);  y = d(:,3);
R  = d(:,5);  P = d(:,6);
tr = t - t(1);

% Momentos de fase (epoch, del log de trajectory_generator)
ph_t = [1784410994.088 1784410997.205 1784411009.762 1784411022.339 1784411029.266];
ph_n = {'RECTA 1','OCHO izq','OCHO der','RECTA 2','FIN'};
ph_r = ph_t - t(1);

% Caido si el roll se fue a +-pi o el pitch supera 0.6 rad
caido = (abs(abs(R)-pi) < 0.5) | (abs(P) > 0.6);
i_caida = find(caido, 1);

fig = figure('Visible','off', 'Position',[60 60 1150 520]);

% --- Trayectoria XY --------------------------------------------------
subplot(1,2,1);
ok = ~caido;
scatter(x(ok), y(ok), 18, tr(ok), 'filled'); hold on;
plot(x, y, '-', 'Color', [0.6 0.6 0.6]);
if ~isempty(i_caida)
    plot(x(i_caida), y(i_caida), 'rx', 'MarkerSize', 14, 'LineWidth', 2.5);
    text(x(i_caida), y(i_caida), '  CAIDA', 'Color','r', 'FontWeight','bold');
end
plot(x(1), y(1), 'ks', 'MarkerFaceColor','g', 'MarkerSize', 9);
text(x(1), y(1), '  inicio');
% marca el inicio de cada fase sobre la trayectoria
for k = 1:numel(ph_r)
    [~, idx] = min(abs(tr - ph_r(k)));
    plot(x(idx), y(idx), 'ko', 'MarkerFaceColor','w');
    text(x(idx), y(idx), ['  ' ph_n{k}], 'FontSize', 8);
end
axis equal; grid on; colorbar; colormap(parula);
xlabel('x [m]'); ylabel('y [m]');
title('Trayectoria ejecutada (color = tiempo [s])');

% --- Pitch vs tiempo -------------------------------------------------
subplot(1,2,2);
plot(tr, rad2deg(P), 'LineWidth', 1.3); hold on;
yline(rad2deg(-0.132552), '--', '\theta_{eq} = -\gamma');
for k = 1:numel(ph_r)
    xline(ph_r(k), ':', ph_n{k}, 'LabelVerticalAlignment','bottom', 'FontSize', 7);
end
grid on; xlabel('t [s]'); ylabel('pitch [deg]');
title('Pitch durante la mision');

saveas(fig, 'mision_resultado.png');
fprintf('Guardado: mision_resultado.png\n');
if ~isempty(i_caida)
    fprintf('CAIDA detectada en t = %.1f s (fase segun marcas de arriba)\n', tr(i_caida));
else
    fprintf('Sin caidas en el registro\n');
end
fprintf('Rango recorrido: x [%.2f, %.2f] m | y [%.2f, %.2f] m\n', ...
        min(x(ok)), max(x(ok)), min(y(ok)), max(y(ok)));
