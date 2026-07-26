cmake_minimum_required(VERSION 3.8)
project(balancing_robot)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(tf2 REQUIRED)

add_executable(lqr_balancer src/lqr_balancer.cpp)
ament_target_dependencies(lqr_balancer
  rclcpp sensor_msgs std_msgs geometry_msgs tf2)

add_executable(trajectory_generator src/trajectory_generator.cpp)
ament_target_dependencies(trajectory_generator rclcpp geometry_msgs)

install(TARGETS lqr_balancer trajectory_generator
  DESTINATION lib/${PROJECT_NAME})

install(DIRECTORY urdf config launch
  DESTINATION share/${PROJECT_NAME})

ament_package()
