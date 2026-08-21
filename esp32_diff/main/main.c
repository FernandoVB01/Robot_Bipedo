/* =============================================================================
 * ROBOT DIFERENCIAL — Firmware ESP32 (ESP-IDF, C nativo)
 * FASE 1: mover el prototipo por SERIAL (tipo WASD).
 *
 * Plataforma estable: 2 ruedas hub (izquierda/derecha) + 2 ruedas de apoyo.
 * Control diferencial:
 *   avanzar  = las dos ruedas igual
 *   girar    = una rueda más que la otra (o en sentidos opuestos = pivota)
 *
 * Ruedas: IZQUIERDA = VESC maestra (UART directo). DERECHA = VESC esclava (CAN id 25).
 *
 * MANEJO (escribí la letra en el Monitor + Enter):
 *   w = adelante      s = atrás
 *   a = girar izq.    d = girar der.
 *   q = curva izq.    e = curva der.
 *   x = frenar
 *   + = más rápido    - = más lento
 *   U<cm>+Enter  = avanzar N cm (odometría)   ·   I<grados>+Enter = girar N° (odometría)
 *   r            = leer el tacómetro (para calibrar)
 *
 * ⚠️ Ruedas en el aire para la primera prueba. Kill switch a mano. VESC con batería.
 * Conexiones VESC: ESP32 GPIO4(TX)→VESC RX, GPIO5(RX)→VESC TX, GND común.
 * =============================================================================
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include <math.h>

/* ═══════════ CONFIGURACIÓN ═══════════ */
#define VESC_CAN_ID_DER   25       /* CAN ID de la rueda DERECHA (esclava) */
#define SIGNO_IZQ         (+1.0f)  /* poné -1 si la rueda izquierda va al revés */
#define SIGNO_DER         (+1.0f)  /* poné -1 si la rueda derecha va al revés */

#define VEL_INI           0.09f    /* velocidad inicial (duty 0..1) */
#define GIRO_DUTY         0.10f    /* duty de giro (diferencial) */
#define VEL_MAX           0.40f    /* tope de velocidad por seguridad */

/* Pines / VESC */
#define VESC_UART   UART_NUM_2
#define VESC_TX     4
#define VESC_RX     5
#define VESC_BAUD   115200
#define COMM_SET_DUTY     5
#define COMM_FORWARD_CAN  34
#define STATUS_LED  GPIO_NUM_2

static const char *TAG = "DIFF";

/* Estado de movimiento (lo setea el serial, lo aplica el lazo) */
static volatile float avance = 0.0f;   /* + adelante, - atrás */
static volatile float giro   = 0.0f;   /* + izquierda, - derecha */
static volatile float VEL    = VEL_INI;

/* ── SEGURIDAD: cada pulso de movimiento dura un tiempo limitado y frena solo ──
 * Ajustá 'pulso_ms' (comando 't') para que un pulso avance ~30 cm. */
static volatile int64_t move_until = 0;   /* hasta cuándo puede moverse (us) */
static volatile int     pulso_ms   = 600; /* duración de cada pulso (ms) */

/* ── ODOMETRÍA: lee el tacómetro de la VESC maestra (rueda IZQUIERDA) para
 *    moverse con EXACTITUD, en lazo cerrado. Comandos serie (con Enter):
 *      U<cm>     avanzar N cm   (negativo = atrás)     ej.  U50
 *      I<grados> girar N grados (positivo/negativo)    ej.  I90   I-90
 *      r         imprime el tacómetro actual (para CALIBRAR)
 *
 *  CALIBRAR (una vez): 'r' para leer el tacómetro, avanzá una distancia MEDIDA
 *  (ej. 100 cm) con 'w', 'r' de nuevo, y calculá:
 *      CUENTAS_POR_MM = (tach_final - tach_inicial) / distancia_en_mm
 *  Poné ese número acá abajo y reflasheá. VIA_MM = separación entre ruedas.   */
#define COMM_GET_VALUES   4
#define OFF_TACH          45       /* offset del tacómetro (int32) en el payload VESC 5.x/6.x */
#define CUENTAS_POR_MM    0.163f   /* calibrado: 49 cuentas / 300 mm (ajustar con F30) */
#define VIA_MM            307.0f   /* ← MEDIR: separación entre ruedas, centro a centro (mm) */

static volatile bool  req_objetivo = false;  /* pedido de movimiento por odometría */
static volatile char  req_tipo     = 0;      /* 'F' avanzar, 'G' girar */
static volatile float req_valor    = 0.0f;   /* cm o grados */
static volatile bool  objetivo_on  = false;  /* hay un movimiento por odometría en curso */
static volatile bool  cancelar_obj = false;  /* WASD/x cancelan el objetivo */
static volatile bool  pedir_tach   = false;  /* comando 'r' */

/* ═══════════ VESC (duty) ═══════════ */
static void vesc_uart_init(void){
    uart_config_t c={.baud_rate=VESC_BAUD,.data_bits=UART_DATA_8_BITS,.parity=UART_PARITY_DISABLE,
        .stop_bits=UART_STOP_BITS_1,.flow_ctrl=UART_HW_FLOWCTRL_DISABLE,.source_clk=UART_SCLK_APB};
    ESP_ERROR_CHECK(uart_driver_install(VESC_UART,256,256,0,NULL,0));
    ESP_ERROR_CHECK(uart_param_config(VESC_UART,&c));
    ESP_ERROR_CHECK(uart_set_pin(VESC_UART,VESC_TX,VESC_RX,UART_PIN_NO_CHANGE,UART_PIN_NO_CHANGE));
}
static uint16_t crc16(const uint8_t*b,unsigned l){uint16_t c=0;
    for(unsigned i=0;i<l;i++){c^=(uint16_t)b[i]<<8;
        for(int k=0;k<8;k++)c=(c&0x8000)?(uint16_t)((c<<1)^0x1021):(uint16_t)(c<<1);}return c;}
static void send_pkt(const uint8_t*p,int len){uint8_t b[64];int i=0;
    b[i++]=0x02;b[i++]=(uint8_t)len;memcpy(&b[i],p,len);i+=len;
    uint16_t c=crc16(p,len);b[i++]=(uint8_t)(c>>8);b[i++]=(uint8_t)(c&0xFF);b[i++]=0x03;
    uart_write_bytes(VESC_UART,(const char*)b,i);}
static int app_duty(uint8_t*p,int i,float duty){int32_t d=(int32_t)(duty*100000.0f);
    p[i++]=COMM_SET_DUTY;p[i++]=(uint8_t)(d>>24);p[i++]=(uint8_t)(d>>16);
    p[i++]=(uint8_t)(d>>8);p[i++]=(uint8_t)d;return i;}
static float clampf(float v,float lo,float hi){return v<lo?lo:(v>hi?hi:v);}

/* Fija el duty de cada rueda: izquierda = maestra (UART), derecha = esclava (CAN) */
static void set_ruedas(float duty_izq, float duty_der){
    duty_izq = clampf(SIGNO_IZQ*duty_izq, -1.0f, 1.0f);
    duty_der = clampf(SIGNO_DER*duty_der, -1.0f, 1.0f);
    /* Rueda izquierda (maestra, UART directo) */
    uint8_t p1[8]; int n1=app_duty(p1,0,duty_izq); send_pkt(p1,n1);
    /* Rueda derecha (esclava, por CAN) */
    uint8_t p2[8]; int n2=0; p2[n2++]=COMM_FORWARD_CAN; p2[n2++]=VESC_CAN_ID_DER;
    n2=app_duty(p2,n2,duty_der); send_pkt(p2,n2);
}

/* Pide COMM_GET_VALUES a la VESC maestra y extrae el tacómetro (int32, big-endian).
 * Devuelve true si leyó un frame válido. SOLO se llama desde tarea_motores, que es
 * la dueña del UART de la VESC (así no hay dos tareas escribiendo el mismo puerto). */
static bool vesc_leer_tach(int32_t *tach){
    uart_flush_input(VESC_UART);
    uint8_t req[1] = { COMM_GET_VALUES };
    send_pkt(req, 1);
    /* Acumula la respuesta hasta ~40 ms: la VESC tarda unos ms en contestar y el
       frame son ~70 bytes. Corta antes si ya llegó lo suficiente. */
    uint8_t buf[128];
    int n = 0;
    int64_t t0 = esp_timer_get_time();
    while(n < (int)sizeof(buf) && (esp_timer_get_time() - t0) < 40000){
        int m = uart_read_bytes(VESC_UART, buf + n, sizeof(buf) - n, pdMS_TO_TICKS(10));
        if(m > 0){ n += m; if(n >= OFF_TACH + 8) break; }
    }
    if(n < 5) return false;
    /* frame: 0x02 <len> <payload...> <crc16> 0x03 ; payload[0]=COMM_GET_VALUES */
    int i = 0;
    while(i < n && buf[i] != 0x02) i++;
    if(i + 2 >= n) return false;
    int len = buf[i+1];
    const uint8_t *pl = &buf[i+2];
    if(i + 2 + len > n) return false;
    if(len <= OFF_TACH + 3 || pl[0] != COMM_GET_VALUES) return false;
    *tach = (int32_t)((uint32_t)pl[OFF_TACH]   << 24 |
                      (uint32_t)pl[OFF_TACH+1] << 16 |
                      (uint32_t)pl[OFF_TACH+2] << 8  |
                      (uint32_t)pl[OFF_TACH+3]);
    return true;
}

/* ═══════════ Tarea: comandos por serial (WASD + odometría) ═══════════ */
static void tarea_serial(void *arg){
    uart_driver_install(UART_NUM_0, 256, 0, 0, NULL, 0);
    static char num[10]; int np=0; char cmd_num=0;   /* captura del número de t/F/G */
    while(1){
        uint8_t ch;
        if(uart_read_bytes(UART_NUM_0,&ch,1,pdMS_TO_TICKS(50))<=0) continue;

        /* ¿Estamos leyendo el número de t/F/G? (permite signo para F-/G-) */
        if(cmd_num){
            if((ch>='0'&&ch<='9')||ch=='-'||ch=='.'){ if(np<(int)sizeof(num)-1) num[np++]=(char)ch; continue; }
            if(ch=='\n'||ch=='\r'){
                num[np]='\0'; float v=atof(num); char c=cmd_num; np=0; cmd_num=0;
                if(c=='t'){ pulso_ms=(int)clampf(v,100,5000); ESP_LOGI(TAG,">> pulso=%d ms",pulso_ms); }
                else if(c=='U'){ req_tipo='U'; req_valor=v; req_objetivo=true; ESP_LOGI(TAG,">> avanzar %.1f cm",v); }
                else if(c=='I'){ req_tipo='I'; req_valor=v; req_objetivo=true; ESP_LOGI(TAG,">> girar %.1f grados",v); }
                continue;
            }
            cmd_num=0; np=0;   /* cualquier otra tecla corta el número y sigue de comando */
        }

        bool es_mov = true;   /* ¿este comando inicia un pulso de movimiento? */
        switch(ch){
            case 'w': case 'W': avance= VEL; giro=0;           cancelar_obj=true; ESP_LOGI(TAG,"ADELANTE %.2f",VEL); break;
            case 's': case 'S': avance=-VEL; giro=0;           cancelar_obj=true; ESP_LOGI(TAG,"ATRAS %.2f",VEL); break;
            case 'a': case 'A': avance=0; giro= GIRO_DUTY;     cancelar_obj=true; ESP_LOGI(TAG,"GIRO IZQ"); break;
            case 'd': case 'D': avance=0; giro=-GIRO_DUTY;     cancelar_obj=true; ESP_LOGI(TAG,"GIRO DER"); break;
            case 'q': case 'Q': avance= VEL; giro= GIRO_DUTY;  cancelar_obj=true; ESP_LOGI(TAG,"CURVA IZQ"); break;
            case 'e': case 'E': avance= VEL; giro=-GIRO_DUTY;  cancelar_obj=true; ESP_LOGI(TAG,"CURVA DER"); break;
            case 'x': case 'X': case ' ': avance=0; giro=0; move_until=0; cancelar_obj=true; es_mov=false; ESP_LOGI(TAG,"FRENAR"); break;
            case '+': VEL=clampf(VEL+0.03f,0.03f,VEL_MAX); es_mov=false; ESP_LOGI(TAG,"VEL=%.2f",VEL); break;
            case '-': VEL=clampf(VEL-0.03f,0.03f,VEL_MAX); es_mov=false; ESP_LOGI(TAG,"VEL=%.2f",VEL); break;
            case 't': case 'T': cmd_num='t'; np=0; es_mov=false; break;   /* t<ms>  duración del pulso */
            case 'U': case 'u': cmd_num='U'; np=0; es_mov=false; break;   /* U<cm>  avanzar (odometría) */
            case 'I': case 'i': cmd_num='I'; np=0; es_mov=false; break;   /* I<deg> girar   (odometría) */
            case 'r': case 'R': pedir_tach=true; es_mov=false; break;     /* leer el tacómetro */
            default:            es_mov=false; break;
        }
        if(es_mov){
            /* Arma el límite de seguridad: se mueve solo 'pulso_ms' y frena solo */
            move_until = esp_timer_get_time() + (int64_t)pulso_ms*1000;
        }
    }
}

/* ═══════════ Lazo de motores (reenvía el duty cada 50 ms) ═══════════ */
static void tarea_motores(void *arg){
    ESP_LOGI(TAG,"Listo. WASD por pulsos + odometría.");
    ESP_LOGI(TAG,"w/s/a/d/q/e=mover · x=frenar · +/-=velocidad · t<ms>=pulso · U<cm>=avanzar · I<deg>=girar · r=tacometro");
    bool frenado=false;
    float obj_izq=0.0f, obj_der=0.0f;
    int32_t tach_ini=0, tach_meta=0, tach;
    int fallos=0;

    while(1){
        /* Comando 'r': imprimir el tacómetro (para calibrar) */
        if(pedir_tach){
            pedir_tach=false;
            if(vesc_leer_tach(&tach)) ESP_LOGI(TAG,"TACOMETRO = %ld", (long)tach);
            else ESP_LOGW(TAG,"no leo el tacometro (VESC encendida? UART2 GPIO4/5 + GND?)");
        }

        /* Arrancar un movimiento por odometría pedido desde el serial */
        if(req_objetivo){
            req_objetivo=false;
            if(vesc_leer_tach(&tach_ini)){
                float mm;
                if(req_tipo=='U'){                        /* avanzar N cm */
                    mm = req_valor * 10.0f;
                    obj_izq = (req_valor>=0.0f)? VEL : -VEL;
                    obj_der = obj_izq;
                } else {                                   /* girar N grados (pivote sobre el eje) */
                    mm = (req_valor/360.0f) * 3.14159265f * VIA_MM;   /* arco que recorre una rueda */
                    obj_izq = (req_valor>=0.0f)? GIRO_DUTY : -GIRO_DUTY;
                    obj_der = -obj_izq;
                }
                tach_meta = (int32_t)fabsf(mm * CUENTAS_POR_MM);
                objetivo_on = (tach_meta > 0);
                cancelar_obj = false;
                fallos = 0;
            } else {
                ESP_LOGW(TAG,"objetivo cancelado: no leo el tacometro");
            }
        }

        /* El control manual (WASD/x) cancela un objetivo en curso */
        if(cancelar_obj){ cancelar_obj=false; objetivo_on=false; }

        float izq, der;
        if(objetivo_on){
            /* LAZO CERRADO: avanza hasta cumplir las cuentas objetivo y frena solo */
            if(vesc_leer_tach(&tach)){
                fallos=0;
                if(labs((long)(tach - tach_ini)) >= tach_meta){
                    objetivo_on=false;
                    ESP_LOGI(TAG,"objetivo cumplido (%ld cuentas)", (long)labs((long)(tach-tach_ini)));
                }
            } else if(++fallos > 25){        /* la VESC no responde: no seguir a ciegas */
                objetivo_on=false; fallos=0;
                ESP_LOGW(TAG,"objetivo abortado: sin telemetria de la VESC");
            }
            izq = objetivo_on? obj_izq : 0.0f;
            der = objetivo_on? obj_der : 0.0f;
            set_ruedas(clampf(izq,-1.0f,1.0f), clampf(der,-1.0f,1.0f));
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        /* ── Modo WASD por pulsos (con auto-stop de seguridad) ── */
        if(esp_timer_get_time() > move_until){
            if(!frenado && (avance!=0.0f || giro!=0.0f)){
                ESP_LOGI(TAG,"auto-stop de seguridad (fin del pulso)");
            }
            avance=0.0f; giro=0.0f; frenado=true;
        } else {
            frenado=false;
        }
        izq = clampf(avance + giro, -1.0f, 1.0f);
        der = clampf(avance - giro, -1.0f, 1.0f);
        set_ruedas(izq, der);
        vTaskDelay(pdMS_TO_TICKS(20));   /* la VESC corta si no recibe por ~1 s */
    }
}

void app_main(void){
    gpio_reset_pin(STATUS_LED);
    gpio_set_direction(STATUS_LED, GPIO_MODE_OUTPUT);
    vesc_uart_init();
    ESP_LOGI(TAG,"====== ROBOT DIFERENCIAL — manejo por serial ======");
    xTaskCreate(tarea_serial,  "serial",  4096, NULL, 5, NULL);
    xTaskCreate(tarea_motores, "motores", 4096, NULL, 6, NULL);
}
