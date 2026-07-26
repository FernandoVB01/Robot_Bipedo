%% =====================================================================
%  FASE 4: ¿Que angulo de rodilla es el mejor?
%  Barre theta_k (angulo interior espinilla-muslo, 180 = pierna recta),
%  recalcula COM, Ib, L_eff, gamma y el LQR para cada angulo, y evalua
%  metricas de control. REEMPLAZA la geometria de ejemplo con tu CAD.
%% =====================================================================
clear; clc;

%% --- Geometria y masas del robot (EJEMPLO: pon las de tu CAD) ---------
l1 = 0.10;   m1 = 0.25;    % espinilla: largo [m] y masa (ambas piernas) [kg]
l2 = 0.10;   m2 = 0.25;    % muslo: largo y masa (ambas piernas)
m3 = 0.70;                 % torso [kg]
t3 = [0.005; 0.050];       % COM del torso respecto a la cadera [x; y] m
tw = 0.04; th = 0.12;      % caja del torso (para su inercia propia)
s1_deg = -8;               % inclinacion fija de la espinilla desde la
                           % vertical (+ = adelante); la fija tu soporte

% Ruedas y entorno (identicos a fase 1)
Mw = 0.3; Iw = 4e-4; R = 0.05; g = 9.81;
tau_max = 2.0;             % limite del motor [N*m]
Qlqr = diag([2 1 120 1]); Rlqr = 1;   % mismos pesos de fase 2

Mb = m1 + m2 + m3;
s1 = deg2rad(s1_deg);

%% --- Barrido ----------------------------------------------------------
tk_deg = 60:2:180;  n = numel(tk_deg);
[Ldat, Gdat, Pdat, TQdat, DMdat, OKdat] = deal(nan(1,n));

for i = 1:n
    tk = deg2rad(tk_deg(i));
    dir_shin  = [sin(s1); cos(s1)];
    knee = l1 * dir_shin;
    a_th = s1 + pi - tk;                 % direccion del muslo (vertical=0)
    hip  = knee + l2 * [sin(a_th); cos(a_th)];

    % Postura invalida: cadera por debajo del tope de la rueda
    if hip(2) < 1.2*R, continue; end
    OKdat(i) = 1;

    % COM compuesto e inercia (varillas + caja, con Steiner)
    c1 = 0.5*knee;  c2 = knee + 0.5*(hip - knee);  c3 = hip + t3;
    COM = (m1*c1 + m2*c2 + m3*c3) / Mb;
    Ib  = m1*l1^2/12 + m1*sum((c1-COM).^2) ...
        + m2*l2^2/12 + m2*sum((c2-COM).^2) ...
        + m3*(tw^2+th^2)/12 + m3*sum((c3-COM).^2);

    L   = hypot(COM(1), COM(2));
    gam = atan2(COM(1), COM(2));

    % Matrices linealizadas (forma cerrada de fase 1)
    alp = Mw + Iw/R^2 + Mb;
    bet = Mb*L;  J = Mb*L^2 + Ib;  dt_ = alp*J - bet^2;
    A = [0 1 0 0; 0 0 -bet*Mb*g*L/dt_ 0; 0 0 0 1; 0 0 alp*Mb*g*L/dt_ 0];
    B = [0; (J/R+bet)/dt_; 0; -(alp+bet/R)/dt_];

    K = lqr(A, B, Qlqr, Rlqr);

    % Recuperacion desde 5 grados: pico de torque en lazo cerrado
    scl = ss(A-B*K, B, eye(4), zeros(4,1));
    [~, ~, xs] = initial(scl, [0; 0; deg2rad(5); 0], 0:0.005:3);
    TQdat(i) = max(abs(xs * K.'));

    Ldat(i)  = L;
    Gdat(i)  = rad2deg(gam);
    Pdat(i)  = max(real(eig(A)));               % polo inestable
    DMdat(i) = asind(min(1, tau_max/(Mb*g*L))); % inclinacion max sostenible
end

%% --- Graficas ---------------------------------------------------------
fig = figure('Visible','off', 'Position',[50 50 1050 640]);

subplot(2,2,1);
yyaxis left;  plot(tk_deg, Ldat, 'LineWidth',1.5); ylabel('L_{eff} [m]');
yyaxis right; plot(tk_deg, Gdat, 'LineWidth',1.5); ylabel('\gamma [deg]');
grid on; xlabel('\theta_{rodilla} [deg]'); title('Geometria del COM');

subplot(2,2,2);
plot(tk_deg, Pdat, 'LineWidth',1.5); grid on;
xlabel('\theta_{rodilla} [deg]'); ylabel('polo inestable [rad/s]');
title('Velocidad de caida (menos = mas facil de controlar)');

subplot(2,2,3);
plot(tk_deg, TQdat, 'LineWidth',1.5); hold on;
yline(tau_max, 'r--', '\tau_{max} motor');
grid on; xlabel('\theta_{rodilla} [deg]'); ylabel('\tau pico [N\cdotm]');
title('Torque pico recuperando 5\circ de inclinacion');

subplot(2,2,4);
plot(tk_deg, DMdat, 'LineWidth',1.5); grid on;
xlabel('\theta_{rodilla} [deg]'); ylabel('\delta\theta_{max} [deg]');
title('Inclinacion maxima sostenible con \tau_{max}');

saveas(fig, 'barrido_rodilla.png');
fprintf('Guardado: barrido_rodilla.png\n\n');

%% --- Tabla y recomendacion -------------------------------------------
fprintf(' theta_k   L_eff[m]  gamma[deg]  polo[rad/s]  tau_pk[Nm]  incl_max[deg]\n');
for i = 1:10:n
    if isnan(OKdat(i)), continue; end
    fprintf('%7d   %8.4f  %9.2f  %11.2f  %10.3f  %12.1f\n', ...
        tk_deg(i), Ldat(i), Gdat(i), Pdat(i), TQdat(i), DMdat(i));
end

% Criterio compuesto de ejemplo: robustez de recuperacion por unidad de
% dificultad dinamica (maximiza inclinacion recuperable, penaliza polo
% rapido y torque pico). Ajusta los pesos a TU prioridad.
Jsc = DMdat ./ (Pdat .* TQdat);
[~, ib] = max(Jsc);
fprintf('\nSugerido con este criterio: theta_k = %d deg', tk_deg(ib));
fprintf('  (L_eff=%.3f m, gamma=%.1f deg, polo=%.1f rad/s, tau_pk=%.2f Nm)\n', ...
        Ldat(ib), Gdat(ib), Pdat(ib), TQdat(ib));
