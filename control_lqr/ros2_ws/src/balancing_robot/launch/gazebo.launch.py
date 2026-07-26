import os
import re

import xacro
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (ExecuteProcess, IncludeLaunchDescription,
                            RegisterEventHandler, TimerAction)
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    pkg_share = get_package_share_directory('balancing_robot')
    xacro_file = os.path.join(pkg_share, 'urdf', 'robot.urdf.xacro')
    robot_xml = xacro.process_file(xacro_file).toxml()

    # gazebo_ros2_control (Humble) pasa el URDF como override de parametro
    # y su parser YAML se rompe con ': ' dentro de comentarios XML.
    # Se eliminan los comentarios antes de publicarlo.
    robot_description = re.sub(r'<!--.*?-->', '', robot_xml, flags=re.S)

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory('gazebo_ros'),
            'launch', 'gazebo.launch.py')))

    rsp = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{'robot_description': robot_description}])

    # Nace en la actitud de equilibrio: pitch = -gamma = -0.132552 rad
    spawn = Node(
        package='gazebo_ros',
        executable='spawn_entity.py',
        arguments=['-topic', 'robot_description',
                   '-entity', 'balancing_robot',
                   '-z', '0.06',
                   '-P', '-0.132552'],
        output='screen')

    jsb_spawner = Node(
        package='controller_manager',
        executable='spawner',
        arguments=['joint_state_broadcaster'])

    effort_spawner = Node(
        package='controller_manager',
        executable='spawner',
        arguments=['effort_controller'])

    balancer = Node(
        package='balancing_robot',
        executable='lqr_balancer',
        output='screen')

    # Mision: recta -> ocho -> recta (los primeros t_estabiliza s quieto)
    trajectory = Node(
        package='balancing_robot',
        executable='trajectory_generator',
        parameters=[{'t_estabiliza': 12.0}],
        output='screen')

    # El robot cae mientras cargan los controladores (el pendulo cae en
    # ~0.3 s y la carga tarda varios segundos). Con el LQR ya corriendo,
    # reset_world lo devuelve a su pose inicial (en equilibrio) y el
    # controlador lo atrapa al instante.
    reset_world = TimerAction(
        period=6.0,
        actions=[ExecuteProcess(
            cmd=['ros2', 'service', 'call', '/reset_world',
                 'std_srvs/srv/Empty'],
            output='screen')])

    # Encadena: spawn -> joint_state_broadcaster -> effort_controller
    #           -> (LQR + mision + reset diferido)
    return LaunchDescription([
        gazebo,
        rsp,
        spawn,
        RegisterEventHandler(OnProcessExit(
            target_action=spawn, on_exit=[jsb_spawner])),
        RegisterEventHandler(OnProcessExit(
            target_action=jsb_spawner, on_exit=[effort_spawner])),
        RegisterEventHandler(OnProcessExit(
            target_action=effort_spawner,
            on_exit=[balancer, trajectory, reset_world])),
    ])
