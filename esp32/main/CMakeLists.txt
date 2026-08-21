# CMakeLists.txt — Componente main (ESP-IDF)
#
# El componente `main` depende implícitamente de todos los demás, así que estos
# REQUIRES no son estrictamente necesarios. Se listan igual para dejar a la
# vista de qué se alimenta el firmware:
#   driver     → uart, gpio
#   nvs_flash  → calibración persistida (comando CAL:)
#   esp_timer  → reloj de microsegundos del lazo de movimiento y del deadman
idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES driver nvs_flash esp_timer
)
