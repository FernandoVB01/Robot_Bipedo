%% Zoom del instante de la caida en FIN (t ~ 51 s)
clear; clc;
D = readmatrix('mision_odom.csv', 'FileType','text');
D = D(all(~isnan(D),2), :);
t = D(:,1) - D(1,1);
vL = D(:,2); vR = D(:,3); yaw = D(:,4); pitch = D(:,5);

m = t > 49.0 & t < 52.5;
fig = figure('Visible','off', 'Position',[40 40 1000 640]);
subplot(3,1,1); plot(t(m), rad2deg(pitch(m)), 'LineWidth',1.2);
grid on; ylabel('pitch [deg]'); title('Zoom de la caida en FIN');
subplot(3,1,2); plot(t(m), vL(m), t(m), vR(m), 'LineWidth',1.1);
grid on; ylabel('\omega rueda [rad/s]'); legend('izq','der');
subplot(3,1,3); plot(t(m), rad2deg(yaw(m)), 'LineWidth',1.1);
grid on; ylabel('yaw [deg]'); xlabel('t [s]');
saveas(fig, 'zoom_fin.png');

fprintf('   t[s]   pitch[deg]   wL[rad/s]   wR[rad/s]   yaw[deg]\n');
for tq = 49.6:0.1:51.6
    [~, i] = min(abs(t - tq));
    fprintf('%7.2f   %9.2f   %9.2f   %9.2f   %8.1f\n', ...
            t(i), rad2deg(pitch(i)), vL(i), vR(i), rad2deg(yaw(i)));
end
