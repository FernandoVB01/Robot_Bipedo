#!/usr/bin/env bash
# =====================================================================
# Instala ROS 2 Humble + Gazebo Classic en Ubuntu 22.04 (WSL2),
# copia el workspace desde Windows y lo compila.
# Uso (dentro de Ubuntu):
#   bash /mnt/c/Users/luisf/OneDrive/Documents/Robot_Bipedo_LQR/ros2_ws/setup_wsl.sh
# =====================================================================
set -e

echo "=== [1/5] Repositorio de ROS 2 ==="
sudo apt update && sudo apt install -y software-properties-common curl
sudo add-apt-repository -y universe
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
     -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
     | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

echo "=== [2/5] ROS 2 Humble + Gazebo + ros2_control (varios GB, paciencia) ==="
sudo apt update
sudo apt install -y ros-humble-desktop ros-humble-gazebo-ros-pkgs \
    ros-humble-gazebo-ros2-control ros-humble-ros2-control \
    ros-humble-ros2-controllers ros-humble-xacro \
    ros-humble-robot-state-publisher python3-colcon-common-extensions

echo "=== [3/5] Copiando workspace desde Windows ==="
cp -r "/mnt/c/Users/luisf/OneDrive/Documents/Robot_Bipedo_LQR/ros2_ws/src" ~/ros2_ws_src_tmp
mkdir -p ~/ros2_ws
mv ~/ros2_ws_src_tmp ~/ros2_ws/src 2>/dev/null || { cp -r ~/ros2_ws_src_tmp/* ~/ros2_ws/src/; rm -rf ~/ros2_ws_src_tmp; }

echo "=== [4/5] Compilando ==="
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
colcon build --symlink-install

echo "=== [5/5] Configurando el entorno ==="
grep -qxF "source /opt/ros/humble/setup.bash" ~/.bashrc || \
    echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
grep -qxF "source ~/ros2_ws/install/setup.bash" ~/.bashrc || \
    echo "source ~/ros2_ws/install/setup.bash" >> ~/.bashrc

echo ""
echo "======================================================"
echo " LISTO. Abre una terminal nueva de Ubuntu y ejecuta:"
echo "   ros2 launch balancing_robot gazebo.launch.py"
echo " El robot: se estabiliza 8s -> recta -> OCHO -> recta"
echo "======================================================"
