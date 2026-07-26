// =====================================================================
// Generador de la mision: estabilizar -> recta -> OCHO -> recta -> alto
//
// El "ocho" son dos circulos completos de sentido opuesto (radio v/w).
// Publica geometry_msgs/Twist en /cmd_vel a 50 Hz:
//   linear.x  = v_ref [m/s]   |   angular.z = w_ref [rad/s]
// El mismo topico sirve para teleoperar el robot a mano.
// =====================================================================
#include <rclcpp/rclcpp.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <cmath>

class TrajectoryGenerator : public rclcpp::Node
{
public:
  TrajectoryGenerator() : Node("trajectory_generator")
  {
    v_       = declare_parameter("v_crucero",   0.20);  // m/s
    w_       = declare_parameter("w_giro",      0.50);  // rad/s -> radio 0.4 m
    t_estab_ = declare_parameter("t_estabiliza", 8.0);  // s balanceandose quieto
    t_recta_ = declare_parameter("t_recta",      5.0);  // s de recta (1 m)

    t_circ_  = 2.0 * M_PI / w_;                         // un circulo completo
    pub_ = create_publisher<geometry_msgs::msg::Twist>("/cmd_vel", 10);
    t0_  = now();
    timer_ = create_wall_timer(std::chrono::milliseconds(20),
                               std::bind(&TrajectoryGenerator::step, this));
    RCLCPP_INFO(get_logger(),
      "Mision: %.0fs estabilizar | %.0fs recta | ocho (2x%.1fs) | %.0fs recta",
      t_estab_, t_recta_, t_circ_, t_recta_);
  }

private:
  void step()
  {
    const double t = (now() - t0_).seconds();
    geometry_msgs::msg::Twist cmd;   // por defecto v=0, w=0

    const double t1 = t_estab_;                 // fin estabilizacion
    const double t2 = t1 + t_recta_;            // fin recta 1
    const double t3 = t2 + t_circ_;             // fin circulo izquierdo
    const double t4 = t3 + t_circ_;             // fin circulo derecho (ocho)
    const double t5 = t4 + t_recta_;            // fin recta 2

    std::string fase;
    if      (t < t1) { fase = "ESTABILIZANDO";                          }
    else if (t < t2) { fase = "RECTA 1";  cmd.linear.x = v_;            }
    else if (t < t3) { fase = "OCHO (circulo izq)";
                       cmd.linear.x = v_;  cmd.angular.z =  w_;         }
    else if (t < t4) { fase = "OCHO (circulo der)";
                       cmd.linear.x = v_;  cmd.angular.z = -w_;         }
    else if (t < t5) { fase = "RECTA 2";  cmd.linear.x = v_;            }
    else             { fase = "FIN (alto y balanceando)";               }

    if (fase != fase_prev_) {
      RCLCPP_INFO(get_logger(), "t=%.1fs -> %s", t, fase.c_str());
      fase_prev_ = fase;
    }
    pub_->publish(cmd);
  }

  double v_, w_, t_estab_, t_recta_, t_circ_;
  std::string fase_prev_;
  rclcpp::Time t0_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr pub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<TrajectoryGenerator>());
  rclcpp::shutdown();
  return 0;
}
