%% =====================================================================
%  FASE 2c: Analisis completo del sistema y del lazo LQR
%  - Estabilidad (polos, amortiguamiento)
%  - Controlabilidad y observabilidad (por sensor)
%  - Respuesta en frecuencia (Bode, margenes, sensibilidad)
%  Genera: analisis_polos.png, analisis_bode_lazo.png
%  Requiere: lqr_design.mat
%% =====================================================================
clear; clc;  load('lqr_design.mat');   % A,B,C,D,Ad,Bd,K,Kd,Ts,gam

fprintf('================ 1. LAZO ABIERTO =================\n');
fprintf('Polos de lazo abierto:\n'); disp(eig(A).');
fprintf('Frecuencia natural del pendulo: %.3f rad/s (%.2f Hz)\n', ...
        max(real(eig(A))), max(real(eig(A)))/(2*pi));

fprintf('\n================ 2. CONTROLABILIDAD ==============\n');
Co = ctrb(A,B);
sv = svd(Co);
fprintf('rango(ctrb) = %d de 4\n', rank(Co));
fprintf('Valores singulares de ctrb: '); fprintf('%.3g  ', sv); fprintf('\n');
fprintf('Numero de condicion: %.3g\n', sv(1)/sv(end));

fprintf('\n================ 3. OBSERVABILIDAD ===============\n');
fprintf('Solo IMU  (mide delta_theta):    rango obsv = %d de 4\n', ...
        rank(obsv(A,[0 0 1 0])));
fprintf('Solo encoders (miden x):         rango obsv = %d de 4\n', ...
        rank(obsv(A,[1 0 0 0])));
fprintf('IMU + encoders (delta_theta, x): rango obsv = %d de 4\n', ...
        rank(obsv(A,[1 0 0 0; 0 0 1 0])));

fprintf('\n================ 4. LAZO CERRADO (tiempo) ========\n');
fprintf('--- Continuo (A - B*K):\n');
damp(ss(A-B*K, B, C, D));
fprintf('--- Discreto a 100 Hz (Ad - Bd*Kd):\n');
damp(ss(Ad-Bd*Kd, Bd, C, D, Ts));

% Offset estacionario ante perturbacion sostenida en la entrada
T_dx = ss(A-B*K, B, [1 0 0 0], 0);       % d(torque) -> x
fprintf('Ganancia DC perturbacion->x: %.4f m/(N*m)\n', dcgain(T_dx));
fprintf('  (con d=0.3 N*m => x_ss = %.4f m)\n', 0.3*dcgain(T_dx));

fprintf('\n================ 5. FRECUENCIA ===================\n');
% Funcion de lazo del LQR: L(s) = K (sI-A)^-1 B  (SISO, realim. negativa)
Lc = ss(A, B, K, 0);
[Gm, Pm, Wcg, Wcp] = margin(Lc);
fprintf('--- Lazo continuo L(s) = K(sI-A)^{-1}B:\n');
fprintf('Margen de ganancia : %.2f dB (en %.2f rad/s)\n', 20*log10(Gm), Wcg);
fprintf('Margen de fase     : %.1f deg (cruce en %.2f rad/s)\n', Pm, Wcp);

% Sensibilidad S = 1/(1+L): pico Ms (robustez, Ms < 2 deseable)
S  = feedback(tf(1), Lc);
Ms = getPeakGain(S);
fprintf('Pico de sensibilidad Ms = %.3f (%.2f dB)\n', Ms, 20*log10(Ms));

% Lazo discreto real implementado a 100 Hz
Ld = ss(Ad, Bd, Kd, 0, Ts);
[Gmd, Pmd, Wcgd, Wcpd] = margin(Ld);
fprintf('--- Lazo discreto (implementacion 100 Hz):\n');
fprintf('Margen de ganancia : %.2f dB (en %.2f rad/s)\n', 20*log10(Gmd), Wcgd);
fprintf('Margen de fase     : %.1f deg (cruce en %.2f rad/s)\n', Pmd, Wcpd);
Sd  = feedback(tf(1,1,Ts), Ld);
Msd = getPeakGain(Sd);
fprintf('Pico de sensibilidad discreto Msd = %.3f (%.2f dB)\n', Msd, 20*log10(Msd));

% Regla de muestreo: ws vs. dinamica mas rapida del lazo cerrado
ws = 2*pi/Ts;
wmax = max(abs(eig(A-B*K)));
fprintf('ws = %.0f rad/s | modo mas rapido lazo cerrado = %.0f rad/s (ratio %.1f)\n', ...
        ws, wmax, ws/wmax);

%% ================ 6. GRAFICAS =========================================
% Mapa de polos: abierto vs cerrado
fig1 = figure('Visible','off', 'Position',[100 100 900 420]);
subplot(1,2,1);
pzmap(ss(A,B,C,D)); grid on; title('Lazo abierto');
subplot(1,2,2);
pzmap(ss(A-B*K,B,C,D)); grid on; title('Lazo cerrado (LQR)');
saveas(fig1, 'analisis_polos.png');

% Bode del lazo con margenes: continuo vs discreto
fig2 = figure('Visible','off', 'Position',[100 100 900 620]);
margin(Lc); hold on;
margin(Ld);
legend('L(s) continuo','L(z) discreto 100 Hz', 'Location','southwest');
grid on;
saveas(fig2, 'analisis_bode_lazo.png');

fprintf('\nGraficas: analisis_polos.png, analisis_bode_lazo.png\n');
