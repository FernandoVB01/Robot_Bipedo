// =====================================================================
// Balanceo LQR + seguimiento de trayectoria (v_ref, w_ref) por /cmd_vel
//
// Arquitectura (estandar en robots balancines de 2 ruedas):
//  - Lazo de balance/avance: LQR planar u = -K * (x - x_ref)
//    con x_ref integrado desde v_ref. El "x" es longitud de arco
//    recorrida (promedio de encoders), valido tambien en curvas.
//  - Lazo de rumbo (yaw): PD sobre el error de rumbo con torque
//    diferencial, desacoplado del balance.
//  - Mezcla:  tau_L = u/2 - tau_psi   |   tau_R = u/2 + tau_psi
// =====================================================================
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <std_msgs/msg/float64_multi_array.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Matrix3x3.h>
#include <array>
#include <algorithm>
#include <cmath>

static double wrapToPi(double a)
{
  while (a >  M_PI) a -= 2.0 * M_PI;
  while (a < -M_PI) a += 2.0 * M_PI;
  return a;
}

class LqrBalancer : public rclcpp::Node
{
public:
  LqrBalancer() : Node("lqr_balancer")
  {
    // ==== Salida de fase2_lqr.m (ganancia DISCRETA Kd, 2026-07-18) ====
    // Valida para p_vals = [1.2 0.3 0.015 4e-4 0.05 9.81 0.020 0.150];
    // si cambias parametros del robot, re-ejecuta fase2_lqr.m y actualiza.
    K_     = {-0.681886, -1.228306, -6.859438, -0.816592};  // [x, dx, d_theta, d_theta_dot]
    gamma_ = 0.132552;   // rad (7.59 deg)
    // ===================================================================

    // Lazo de rumbo (ajustables por parametro): wn ~3 rad/s, zeta ~0.8
    kp_yaw_ = declare_parameter("kp_yaw", 0.02);
    kd_yaw_ = declare_parameter("kd_yaw", 0.01);

    imu_sub_ = create_subscription<sensor_msgs::msg::Imu>(
      "/imu", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::Imu::SharedPtr msg) {
        tf2::Quaternion q(msg->orientation.x, msg->orientation.y,
                          msg->orientation.z, msg->orientation.w);
        double roll;
        tf2::Matrix3x3(q).getRPY(roll, pitch_, yaw_);
        // Satura a tasas fisicamente posibles: un teleport (reset_world)
        // produce picos falsos de ~1000 rad/s que detonan el LQR
        pitch_rate_ = std::clamp(msg->angular_velocity.y, -10.0, 10.0);
        yaw_rate_   = std::clamp(msg->angular_velocity.z, -10.0, 10.0);
        imu_ok_ = true;
      });

    joint_sub_ = create_subscription<sensor_msgs::msg::JointState>(
      "/joint_states", 10,
      [this](sensor_msgs::msg::JointState::SharedPtr msg) {
        double pos_sum = 0.0, vel_sum = 0.0;
        for (size_t i = 0; i < msg->name.size(); ++i) {
          if (msg->name[i] == "left_wheel_joint" ||
              msg->name[i] == "right_wheel_joint") {
            pos_sum += msg->position[i];
            vel_sum += msg->velocity[i];
          }
        }
        x_  = WHEEL_R * pos_sum / 2.0;   // longitud de arco recorrida
        dx_ = WHEEL_R * vel_sum / 2.0;
      });

    // Referencias de mision: linear.x = v_ref [m/s], angular.z = w_ref [rad/s]
    cmd_vel_sub_ = create_subscription<geometry_msgs::msg::Twist>(
      "/cmd_vel", 10,
      [this](geometry_msgs::msg::Twist::SharedPtr msg) {
        v_cmd_ = msg->linear.x;
        w_cmd_ = msg->angular.z;
      });

    cmd_pub_ = create_publisher<std_msgs::msg::Float64MultiArray>(
      "/effort_controller/commands", 10);

    // Lazo de control a 100 Hz (= Ts del diseno en MATLAB)
    timer_ = create_wall_timer(std::chrono::milliseconds(10),
                               std::bind(&LqrBalancer::controlLoop, this));
  }

private:
  void controlLoop()
  {
    if (!imu_ok_) return;                     // espera la primera lectura

    // --- Integracion de referencias (rampas seguibles por el LQR) ----
    // Robot caido: fuera del rango de recuperacion del LQR lineal.
    // Torque cero (evita que las ruedas giren a tope contra el suelo)
    // hasta que un reset_world lo devuelva cerca del equilibrio.
    if (std::abs(pitch_ + gamma_) > 0.6) {
      rearm_ = 15;                            // 0.15 s de re-armado al volver
      std_msgs::msg::Float64MultiArray stop;
      stop.data = {0.0, 0.0};
      cmd_pub_->publish(stop);
      return;
    }

    // Recien recuperado (p.ej. tras reset_world): re-inicializa las
    // referencias al estado actual y deja asentar los sensores antes de
    // cerrar el lazo
    if (rearm_ > 0) {
      --rearm_;
      x_ref_ = x_;  psi_ref_ = yaw_;  v_ref_ = 0.0;  w_ref_ = 0.0;
      std_msgs::msg::Float64MultiArray stop;
      stop.data = {0.0, 0.0};
      cmd_pub_->publish(stop);
      return;
    }

    // Rampa suave hacia las consignas (los escalones de velocidad son
    // transitorios violentos para un pendulo invertido)
    v_ref_ += std::clamp(v_cmd_ - v_ref_, -ACC_V * DT, ACC_V * DT);
    w_ref_ += std::clamp(w_cmd_ - w_ref_, -ACC_W * DT, ACC_W * DT);

    x_ref_   += v_ref_ * DT;
    psi_ref_  = wrapToPi(psi_ref_ + w_ref_ * DT);
    if (!psi_init_) { psi_ref_ = yaw_; psi_init_ = true; }

    // --- Lazo de balance + avance: u = -K * (estado - referencia) ----
    const double d_theta = pitch_ + gamma_;   // equilibrio -> d_theta = 0
    // Errores saturados: nunca sostener la saturacion del actuador por
    // estados absurdos (ruedas embaladas, saltos de referencia)
    const std::array<double, 4> err = {
        std::clamp(x_ - x_ref_, -0.5, 0.5),
        std::clamp(dx_ - v_ref_, -1.0, 1.0),
        d_theta, pitch_rate_};
    double u = 0.0;
    for (size_t i = 0; i < 4; ++i) u -= K_[i] * err[i];
    u = std::clamp(u, -MAX_TAU, MAX_TAU);

    // --- Lazo de rumbo: PD con torque diferencial --------------------
    const double yaw_err = wrapToPi(psi_ref_ - yaw_);
    double tau_psi = kp_yaw_ * yaw_err + kd_yaw_ * (w_ref_ - yaw_rate_);
    tau_psi = std::clamp(tau_psi, -MAX_TAU_YAW, MAX_TAU_YAW);

    // --- Mezcla y saturacion por rueda -------------------------------
    std_msgs::msg::Float64MultiArray cmd;
    cmd.data = {std::clamp(u / 2.0 - tau_psi, -MAX_TAU, MAX_TAU),    // left
                std::clamp(u / 2.0 + tau_psi, -MAX_TAU, MAX_TAU)};   // right
    cmd_pub_->publish(cmd);
  }

  static constexpr double DT          = 0.01;  // = Ts de MATLAB
  static constexpr double WHEEL_R     = 0.05;  // = R de MATLAB
  static constexpr double MAX_TAU     = 2.0;   // = limites del URDF
  static constexpr double MAX_TAU_YAW = 0.3;   // el giro nunca roba el
                                               // torque que pide el balance
  static constexpr double ACC_V = 0.3;         // m/s^2 rampa de velocidad
  static constexpr double ACC_W = 1.5;         // rad/s^2 rampa de giro
  std::array<double, 4> K_;
  double gamma_{0.0};
  double kp_yaw_{0.0}, kd_yaw_{0.0};

  double pitch_{0.0}, pitch_rate_{0.0}, yaw_{0.0}, yaw_rate_{0.0};
  double x_{0.0}, dx_{0.0};
  double v_cmd_{0.0}, w_cmd_{0.0};
  double v_ref_{0.0}, w_ref_{0.0}, x_ref_{0.0}, psi_ref_{0.0};
  int rearm_{0};
  bool imu_ok_{false}, psi_init_{false};

  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_sub_;
  rclcpp::Subscription<sensor_msgs::msg::JointState>::SharedPtr joint_sub_;
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr cmd_vel_sub_;
  rclcpp::Publisher<std_msgs::msg::Float64MultiArray>::SharedPtr cmd_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<LqrBalancer>());
  rclcpp::shutdown();
  return 0;
}
