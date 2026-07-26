%% =====================================================================
%  FASE 2: LQR continuo, discretizacion a 100 Hz y LQR discreto
%  Requiere: modelo_robot.mat (generado por fase1_modelado.m)
%% =====================================================================
clear; clc;  load('modelo_robot.mat');

%% --- 1. Pesos LQR (regla de Bryson como punto de partida) -------------
% Prioridad: mantener d_theta pequeno >> regular posicion
%             x       dx      d_theta   d_theta_dot
Qlqr = diag([ 2,      1,      120,      1 ]);
Rlqr = 1;                                 % penaliza torque (satura ~ Nm)

K = lqr(A, B, Qlqr, Rlqr);
fprintf('K continua = [%.3f  %.3f  %.3f  %.3f]\n', K);
fprintf('Polos lazo cerrado (continuo):\n'); disp(eig(A - B*K));

%% --- 2. Discretizacion a 100 Hz ---------------------------------------
Ts    = 0.01;                             % 100 Hz
sys_d = c2d(ss(A,B,C,D), Ts, 'zoh');
[Ad, Bd, Cd, Dd] = ssdata(sys_d);

% A 100 Hz la K continua funciona, pero la ganancia correcta para el
% controlador digital se obtiene con el equivalente discreto:
Kd = dlqr(Ad, Bd, Qlqr, Rlqr);
fprintf('K discreta = [%.3f  %.3f  %.3f  %.3f]\n', Kd);
fprintf('Polos lazo cerrado (discreto, |z|<1 = estable):\n');
disp(abs(eig(Ad - Bd*Kd)));

%% --- 3. Exporta para Simulink y ROS -----------------------------------
save('lqr_design.mat', 'A','B','C','D','Ad','Bd','K','Kd','Ts','gam');
fprintf('\n>> Copia estas lineas en el nodo C++ (Fase 3):\n');
fprintf('   K_     = {%.6f, %.6f, %.6f, %.6f};\n', Kd);
fprintf('   gamma_ = %.6f;\n', gam);
