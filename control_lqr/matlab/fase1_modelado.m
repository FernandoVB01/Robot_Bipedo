%% =====================================================================
%  FASE 1: Modelado simbolico del pendulo invertido movil con COM
%  desplazado (chasis con rodilla fija = solido rigido irregular)
%  Estado: X = [x; dx; d_theta; d_theta_dot], con d_theta = theta + gamma
%% =====================================================================
clear; clc;

%% --- 1. Geometria del COM: L_eff y gamma ------------------------------
syms x_com y_com real
L_eff_sym = sqrt(x_com^2 + y_com^2);
gamma_sym = atan2(x_com, y_com);      % angulo de desfase estatico

%% --- 2. Parametros y coordenadas generalizadas ------------------------
syms Mb Mw Ib Iw R g tau real         % Mw, Iw: TOTALES (ambas ruedas)
syms xw dxw ddxw th dth ddth real     % xw: traslacion, th: inclinacion

L  = L_eff_sym;
gm = gamma_sym;

% Cinematica del COM (la gravedad actua sobre sin(theta + gamma))
xb  = xw + L*sin(th + gm);
dxb = dxw + L*cos(th + gm)*dth;
dyb = -L*sin(th + gm)*dth;

%% --- 3. Lagrangiano ---------------------------------------------------
T = 1/2*(Mw + Iw/R^2)*dxw^2 ...       % ruedas: traslacion + rotacion
  + 1/2*Mb*(dxb^2 + dyb^2) ...        % cuerpo: traslacion del COM
  + 1/2*Ib*dth^2;                     % cuerpo: rotacion sobre el COM
V   = Mb*g*L*cos(th + gm);
Lag = T - V;

%% --- 4. Ecuaciones de Euler-Lagrange ----------------------------------
q   = [xw; th];   dq  = [dxw; dth];   ddq = [ddxw; ddth];

% d/dt(dL/ddq) - dL/dq = Q, expandido con regla de la cadena:
EL = jacobian(jacobian(Lag, dq).', [q; dq]) * [dq; ddq] ...
   - jacobian(Lag, q).';

% Fuerzas generalizadas: el motor actua entre cuerpo y rueda
Q_gen = [tau/R; -tau];

sol  = solve(EL == Q_gen, ddq);       % despeja ddxw y ddth
f_nl = [dxw; simplify(sol.ddxw); dth; simplify(sol.ddth)];  % dX = f(X,u)

%% --- 5. Linealizacion en el equilibrio dinamico theta = -gamma --------
X = [xw; dxw; th; dth];   U = tau;
A_sym = jacobian(f_nl, X);
B_sym = jacobian(f_nl, U);

eq_vars = [dxw, th,  dth, tau];
eq_vals = [0,   -gm, 0,   0  ];       % tau_eq = 0: en theta=-gamma no hay
                                      % torque gravitatorio residual
A_lin = simplify(subs(A_sym, eq_vars, eq_vals));
B_lin = simplify(subs(B_sym, eq_vars, eq_vals));

disp('A simbolica (coordenada 3 = delta_theta = theta + gamma):');
pretty(A_lin)
disp('B simbolica:'); pretty(B_lin)

%% --- 6. Sustitucion numerica (REEMPLAZA con datos de tu CAD/bascula) --
p_syms = [Mb,  Mw,  Ib,    Iw,   R,    g,    x_com, y_com];
p_vals = [1.2, 0.3, 0.015, 4e-4, 0.05, 9.81, 0.020, 0.150];

L_eff = double(subs(L_eff_sym, [x_com y_com], p_vals(7:8)));
gam   = double(subs(gamma_sym, [x_com y_com], p_vals(7:8)));
fprintf('L_eff = %.4f m | gamma = %.4f rad (%.2f deg)\n', ...
        L_eff, gam, rad2deg(gam));

A = double(subs(A_lin, p_syms, p_vals));
B = double(subs(B_lin, p_syms, p_vals));
C = eye(4);            % suponemos estado completo medible
                       % (x, dx: encoders | d_theta, d_theta_dot: IMU)
D = zeros(4,1);

%% --- 7. Modelo de espacio de estados y verificaciones -----------------
sys_c = ss(A, B, C, D, ...
    'StateName', {'x','dx','d_theta','d_theta_dot'}, ...
    'InputName', 'tau', 'OutputName', {'x','dx','d_theta','d_theta_dot'});

fprintf('Polos en lazo abierto:\n');  disp(eig(A));   % uno sera > 0
assert(rank(ctrb(A,B)) == 4, 'El sistema NO es controlable');
fprintf('Sistema controlable (rango ctrb = 4)\n');

save('modelo_robot.mat', 'A','B','C','D','sys_c','L_eff','gam','p_vals');
