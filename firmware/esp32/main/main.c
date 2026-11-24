#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <errno.h>
#include "freertos/queue.h"
#include "driver/i2s_std.h"
#include "driver/i2c.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"

#include "driver/pulse_cnt.h"
#include "wire_codec.h"

typedef struct {
    int id, din, sda, scl, bclk, ws;
    i2s_chan_handle_t rx;
    volatile uint32_t overflows, frames, nonzero_words, changing_words, ones_words;
    uint32_t queue_drops, stale_drops, send_errors, send_drops;
    int last_send_errno;
    bool bus_ready, gate_initialized;
} board_t;
static board_t boards[2]={{.id=0,.din=34,.sda=21,.scl=22,.bclk=26,.ws=25},{.id=1,.din=35,.sda=32,.scl=33,.bclk=26,.ws=25}};
static int udp_sock=-1;
static struct sockaddr_in subscriber;
static bool subscribed, started, station_requested;
static int station_retries;
static bool dfu_active,dfu_finished;
static uint32_t dfu_next_block;
static portMUX_TYPE subscriber_mux=portMUX_INITIALIZER_UNLOCKED;
static const char *TAG="XVFDual";
typedef struct __attribute__((packed)) {
    char magic[4];uint32_t sequence;uint64_t delivered_frame;
    uint16_t frames,payload_bytes;uint64_t delivery_time_us;uint32_t rx_overflows;
} packet_header_t;

static bool overflow_cb(i2s_chan_handle_t h,i2s_event_data_t *e,void *user) {
    ((board_t*)user)->overflows++;return false;
}
static esp_err_t control_clock_high(board_t *b) {
    gpio_set_level(b->scl,1);
    int64_t deadline=esp_timer_get_time()+100000,next_yield=esp_timer_get_time()+1000;
    while (!gpio_get_level(b->scl)) {
        int64_t now=esp_timer_get_time();
        if (now>deadline) return ESP_ERR_TIMEOUT;
        if (now>next_yield) {vTaskDelay(1);next_yield=esp_timer_get_time()+1000;}
        else esp_rom_delay_us(2);
    }
    return ESP_OK;
}
static esp_err_t control_start(board_t *b) {
    gpio_set_level(b->sda,1);esp_rom_delay_us(20);
    esp_err_t err=control_clock_high(b);if (err!=ESP_OK) return err;
    esp_rom_delay_us(20);gpio_set_level(b->sda,0);esp_rom_delay_us(20);gpio_set_level(b->scl,0);return ESP_OK;
}
static esp_err_t control_stop(board_t *b) {
    gpio_set_level(b->scl,0);gpio_set_level(b->sda,0);esp_rom_delay_us(20);
    esp_err_t err=control_clock_high(b);esp_rom_delay_us(20);gpio_set_level(b->sda,1);esp_rom_delay_us(200);return err;
}
static esp_err_t control_send_byte(board_t *b,uint8_t byte) {
    for (int bit=0;bit<8;bit++) {
        gpio_set_level(b->sda,(byte&0x80)!=0);esp_rom_delay_us(20);
        esp_err_t err=control_clock_high(b);if (err!=ESP_OK) return err;
        esp_rom_delay_us(20);gpio_set_level(b->scl,0);byte<<=1;
    }
    gpio_set_level(b->sda,1);esp_rom_delay_us(20);
    esp_err_t err=control_clock_high(b);if (err!=ESP_OK) return err;
    esp_rom_delay_us(20);bool ack=!gpio_get_level(b->sda);gpio_set_level(b->scl,0);
    return ack?ESP_OK:ESP_FAIL;
}
static esp_err_t control_receive_byte(board_t *b,uint8_t *byte,bool last) {
    uint8_t value=0;gpio_set_level(b->sda,1);
    for (int bit=0;bit<8;bit++) {
        esp_rom_delay_us(20);esp_err_t err=control_clock_high(b);if (err!=ESP_OK) return err;
        esp_rom_delay_us(20);value=(value<<1)|gpio_get_level(b->sda);gpio_set_level(b->scl,0);
    }
    gpio_set_level(b->sda,last?1:0);esp_rom_delay_us(20);
    esp_err_t err=control_clock_high(b);if (err!=ESP_OK) return err;
    esp_rom_delay_us(20);gpio_set_level(b->scl,0);gpio_set_level(b->sda,1);*byte=value;return ESP_OK;
}
static esp_err_t control_transfer(board_t *b,const uint8_t *write,size_t wn,uint8_t *read,size_t rn) {
    esp_err_t err=control_start(b);
    if (wn && err==ESP_OK) err=control_send_byte(b,0x58);
    for (size_t i=0;i<wn && err==ESP_OK;i++) err=control_send_byte(b,write[i]);
    if (rn && wn && err==ESP_OK) err=control_start(b);
    if (rn && err==ESP_OK) err=control_send_byte(b,0x59);
    for (size_t i=0;i<rn && err==ESP_OK;i++) err=control_receive_byte(b,&read[i],i+1==rn);
    esp_err_t stop=control_stop(b);return err==ESP_OK?stop:err;
}
static esp_err_t xvf_read(board_t *b,uint8_t resource,uint8_t cmd,uint8_t *out,size_t n) {
    uint8_t header[3]={resource,(uint8_t)(cmd|0x80),(uint8_t)(n+1)},reply[64];
    if (n+1>sizeof(reply)) return ESP_ERR_INVALID_SIZE;
    for (int retry=0;retry<20;retry++) {
        esp_err_t err=control_transfer(b,header,3,reply,n+1);
        if (err!=ESP_OK) {ESP_LOGW(TAG,"B%d read r%u c%u transfer=%s",b->id,resource,cmd,esp_err_to_name(err));return err;}
        if (reply[0]==0) {memcpy(out,reply+1,n);return ESP_OK;}
        if (reply[0]!=64) {ESP_LOGW(TAG,"B%d read r%u c%u status%u",b->id,resource,cmd,reply[0]);return ESP_FAIL;}
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return ESP_ERR_TIMEOUT;
}
static esp_err_t xvf_write(board_t *b,uint8_t resource,uint8_t cmd,const uint8_t *value,size_t n) {
    uint8_t frame[160]={resource,cmd,(uint8_t)n};
    if (n+3>sizeof(frame)) return ESP_ERR_INVALID_SIZE;
    memcpy(frame+3,value,n);
    for (int retry=0;retry<20;retry++) {
        esp_err_t err=control_transfer(b,frame,n+3,NULL,0);
        if (err!=ESP_OK) {ESP_LOGW(TAG,"B%d write r%u c%u transfer=%s",b->id,resource,cmd,esp_err_to_name(err));return err;}
        /* REBOOT can remove the control endpoint before its status is read. */
        if (resource==48 && cmd==7) return ESP_OK;
        vTaskDelay(pdMS_TO_TICKS(10));
        uint8_t status;err=control_transfer(b,NULL,0,&status,1);
        if (err!=ESP_OK) {ESP_LOGW(TAG,"B%d write r%u c%u status_read=%s",b->id,resource,cmd,esp_err_to_name(err));return err;}
        if (!status) return ESP_OK;
        if (status!=64 || resource==240) {
            ESP_LOGW(TAG,"B%d write r%u c%u status%u",b->id,resource,cmd,status);return ESP_FAIL;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return ESP_ERR_TIMEOUT;
}
static esp_err_t init_control(board_t *b) {
    if (!b->bus_ready) {
        gpio_config_t recovery={.pin_bit_mask=(1ULL<<b->sda)|(1ULL<<b->scl),
            .mode=GPIO_MODE_INPUT_OUTPUT_OD,.pull_up_en=GPIO_PULLUP_ENABLE};
        ESP_ERROR_CHECK(gpio_config(&recovery));gpio_set_level(b->sda,1);gpio_set_level(b->scl,1);
        for (int i=0;i<9;i++) {
            gpio_set_level(b->scl,0);esp_rom_delay_us(10);
            esp_err_t high=control_clock_high(b);if (high!=ESP_OK) return high;
            esp_rom_delay_us(10);
        }
        esp_err_t err=control_stop(b);if (err!=ESP_OK) return err;
        b->bus_ready=true;
    }
    uint8_t version[3];esp_err_t err=xvf_read(b,48,0,version,3);
    if (err==ESP_OK) ESP_LOGI(TAG,"Board %d firmware %u.%u.%u",b->id,version[0],version[1],version[2]);
    return err;
}
static esp_err_t set_microphone_gate(board_t *b,uint8_t muted) {
    esp_err_t err;
    if (b->id==0) {
        uint8_t value[2]={30,muted},actual[5];err=xvf_write(b,20,1,value,2);
        if (err==ESP_OK) err=xvf_read(b,20,0,actual,5);
        if (err==ESP_OK && actual[1]!=muted) err=ESP_FAIL;
    } else {
        /* XMOS reference PORT0 is XS1_PORT_8C. X0D30 is bit 4, not pin index 30. */
        uint8_t select[8]={0,0,0,0,4,0,0,0},active[4]={1,0,0,0},value[3]={0,4,muted},duty;
        /* IDs verified from the v3.2.1 release's official command_map.dll. */
        err=xvf_write(b,20,0,select,8);
        if (err==ESP_OK) err=xvf_write(b,20,2,active,4);
        if (err==ESP_OK) err=xvf_write(b,20,1,value,3);
        if (err==ESP_OK) err=xvf_read(b,20,2,active,4);
        if (err==ESP_OK) err=xvf_read(b,20,3,&duty,1);
        if (err==ESP_OK && (active[0]!=1 || active[1] || active[2] || active[3] || duty!=(muted?100:0))) err=ESP_FAIL;
    }
    ESP_LOGI(TAG,"Board %d physical microphone gate muted=%u readback=%s",b->id,muted,esp_err_to_name(err));
    return err;
}
static esp_err_t configure_packed(board_t *b,int muted_mic) {
    uint8_t up[2]={0,0},route[12]={1,0,1,1,1,2,1,3,0,0,0,0},packed[2]={1,1};
    if (muted_mic>=0 && muted_mic<4) {route[2*muted_mic]=0;route[2*muted_mic+1]=0;}
    esp_err_t err=ESP_OK;
    if (!b->gate_initialized) {
        err=set_microphone_gate(b,0);
        b->gate_initialized=err==ESP_OK;
    }
    if (err==ESP_OK) err=xvf_write(b,35,14,up,2);
    if (err==ESP_OK) err=xvf_write(b,35,23,route,12);
    if (err==ESP_OK) err=xvf_write(b,35,13,packed,2);
    if (err!=ESP_OK) return err;
    uint8_t actual[12],enabled[2];
    if (err==ESP_OK) err=xvf_read(b,35,23,actual,12);
    if (err==ESP_OK) err=xvf_read(b,35,13,enabled,2);
    if (err!=ESP_OK) return err;
    bool match=!memcmp(actual,route,12) && enabled[0]==1 && enabled[1]==1;
    ESP_LOGI(TAG,"Board %d packed routing matches=%d",b->id,match);
    return match?ESP_OK:ESP_FAIL;
}
static void dfu_status(void) {
    board_t *b=&boards[1];esp_err_t err=init_control(b);
    ESP_LOGI(TAG,"DFU B1 control result=%s",esp_err_to_name(err));
    if (!b->bus_ready) return;
    uint8_t version[3],status[5],state;
    err=xvf_read(b,240,88,version,3);
    if (err==ESP_OK) ESP_LOGI(TAG,"DFU B1 version=%u.%u.%u",version[0],version[1],version[2]);
    err=xvf_read(b,240,3,status,5);
    if (err==ESP_OK) ESP_LOGI(TAG,"DFU B1 status=%u timeout_bytes=%u,%u,%u state=%u",status[0],status[1],status[2],status[3],status[4]);
    err=xvf_read(b,240,5,&state,1);
    if (err==ESP_OK) ESP_LOGI(TAG,"DFU B1 state=%u",state);
}
static esp_err_t dfu_wait(uint8_t wanted) {
    int64_t deadline=esp_timer_get_time()+30000000;
    while (esp_timer_get_time()<deadline) {
        uint8_t status[5];esp_err_t err=xvf_read(&boards[1],240,3,status,5);
        if (err!=ESP_OK) return err;
        if (status[0] || status[4]==10) {ESP_LOGE(TAG,"DFU error status=%u state=%u",status[0],status[4]);return ESP_FAIL;}
        uint32_t ms=status[1]|((uint32_t)status[2]<<8)|((uint32_t)status[3]<<16);
        if (ms>10000) return ESP_ERR_TIMEOUT;
        if (ms) vTaskDelay(pdMS_TO_TICKS(ms)+1);
        if (status[4]==wanted) return ESP_OK;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return ESP_ERR_TIMEOUT;
}
static void dfu_begin(void) {
    dfu_active=false;dfu_finished=false;dfu_next_block=0;
    esp_err_t err=init_control(&boards[1]);
    if (err==ESP_OK) err=dfu_wait(2);
    /* The only writable DFU target exposed by this bridge is upgrade alt 1. */
    const uint8_t alternate=1;
    if (err==ESP_OK) err=xvf_write(&boards[1],240,64,&alternate,1);
    if (err==ESP_OK) err=dfu_wait(2);
    dfu_active=err==ESP_OK;
    ESP_LOGI(TAG,"DFU BEGIN upgrade_alt=1 result=%s",esp_err_to_name(err));
}
static void dfu_block(unsigned index,const char *hex) {
    if (!dfu_active || index!=dfu_next_block || strlen(hex)!=256) {
        ESP_LOGE(TAG,"DFU BLOCK rejected index=%u expected=%lu",index,(unsigned long)dfu_next_block);return;
    }
    uint8_t payload[130]={128,0};
    for (int i=0;i<128;i++) {
        char pair[3]={hex[2*i],hex[2*i+1],0};char *end;
        unsigned long value=strtoul(pair,&end,16);
        if (*end || value>255) {ESP_LOGE(TAG,"DFU invalid hex");return;}
        payload[i+2]=value;
    }
    esp_err_t err=xvf_write(&boards[1],240,1,payload,sizeof(payload));
    if (err==ESP_OK) err=dfu_wait(5);
    if (err==ESP_OK) {dfu_next_block++;ESP_LOGI(TAG,"DFU BLOCK ACK index=%u",index);}
    else {dfu_active=false;ESP_LOGE(TAG,"DFU BLOCK FAILED index=%u result=%s",index,esp_err_to_name(err));}
}
static void dfu_end(void) {
    if (!dfu_active || !dfu_next_block) {ESP_LOGE(TAG,"DFU END rejected");return;}
    uint8_t payload[130]={0};esp_err_t err=xvf_write(&boards[1],240,1,payload,sizeof(payload));
    if (err==ESP_OK) err=dfu_wait(2);
    dfu_finished=err==ESP_OK;dfu_active=false;
    ESP_LOGI(TAG,"DFU END blocks=%lu result=%s",(unsigned long)dfu_next_block,esp_err_to_name(err));
}
static void clock_diagnostic(void);
static void diagnostic(void) {
    for (int i=0;i<2;i++) {
        board_t *b=&boards[i];esp_err_t err=init_control(b);
        if (err==ESP_OK) {
            uint8_t build[51]={0};esp_err_t build_err=xvf_read(b,48,1,build,50);
            if (build_err==ESP_OK) ESP_LOGI(TAG,"DIAG B%d build=%s",i,(char*)build);
        }
        ESP_LOGI(TAG,"DIAG B%d SDA%d=%d SCL%d=%d version_result=%s",i,b->sda,gpio_get_level(b->sda),b->scl,gpio_get_level(b->scl),esp_err_to_name(err));
    }
    clock_diagnostic();
}
static void clock_diagnostic(void) {
    gpio_config_t clocks={.pin_bit_mask=(1ULL<<26)|(1ULL<<25)|(1ULL<<13)|(1ULL<<14),.mode=GPIO_MODE_INPUT};
    ESP_ERROR_CHECK(gpio_config(&clocks));
    int last_clk=gpio_get_level(26),last_ws=gpio_get_level(25);unsigned changes_clk=0,changes_ws=0;
    int64_t end=esp_timer_get_time()+10000;
    while (esp_timer_get_time()<end) {
        int clk=gpio_get_level(26),ws=gpio_get_level(25);
        changes_clk+=clk!=last_clk;changes_ws+=ws!=last_ws;last_clk=clk;last_ws=ws;
    }
    ESP_LOGI(TAG,"DIAG 10ms software observations: BCLK transitions=%u WS transitions=%u (not a frequency measurement)",changes_clk,changes_ws);
    int clock_pins[4]={26,25,13,14};
    for (int pin_index=0;pin_index<4;pin_index++) {
        int pin=clock_pins[pin_index];
        pcnt_unit_config_t unit_cfg={.low_limit=-32767,.high_limit=32767};
        pcnt_unit_handle_t unit=NULL;pcnt_channel_handle_t channel=NULL;
        ESP_ERROR_CHECK(pcnt_new_unit(&unit_cfg,&unit));
        pcnt_chan_config_t channel_cfg={.edge_gpio_num=pin,.level_gpio_num=-1};
        ESP_ERROR_CHECK(pcnt_new_channel(unit,&channel_cfg,&channel));
        ESP_ERROR_CHECK(pcnt_channel_set_edge_action(channel,PCNT_CHANNEL_EDGE_ACTION_INCREASE,PCNT_CHANNEL_EDGE_ACTION_HOLD));
        ESP_ERROR_CHECK(pcnt_unit_enable(unit));ESP_ERROR_CHECK(pcnt_unit_clear_count(unit));
        ESP_ERROR_CHECK(pcnt_unit_start(unit));int64_t begin=esp_timer_get_time();
        esp_rom_delay_us(5000);int64_t elapsed=esp_timer_get_time()-begin;
        ESP_ERROR_CHECK(pcnt_unit_stop(unit));int count=0;ESP_ERROR_CHECK(pcnt_unit_get_count(unit,&count));
        ESP_LOGI(TAG,"PCNT GPIO%d rising_edges=%d interval_us=%lld estimated_hz=%.1f",pin,count,(long long)elapsed,(double)count*1000000/elapsed);
        ESP_ERROR_CHECK(pcnt_unit_disable(unit));ESP_ERROR_CHECK(pcnt_del_channel(channel));ESP_ERROR_CHECK(pcnt_del_unit(unit));
    }
}
enum { TX_QUEUE_DEPTH = 24, TX_RETRY_US = 20000, TX_MAX_AGE_US = 60000 };

typedef struct {
    packet_header_t header;
    uint32_t words[256];
    uint32_t epoch;
} audio_packet_t;

static StaticQueue_t tx_queue_state;
static uint8_t tx_queue_storage[TX_QUEUE_DEPTH * sizeof(audio_packet_t)];
static QueueHandle_t tx_queue;
static uint32_t subscriber_epoch;
static uint32_t tx_hold_requested;

static bool subscription_snapshot(struct sockaddr_in *address, uint32_t *epoch) {
    taskENTER_CRITICAL(&subscriber_mux);
    bool active = subscribed;
    if (address) *address = subscriber;
    if (epoch) *epoch = subscriber_epoch;
    taskEXIT_CRITICAL(&subscriber_mux);
    return active;
}

static void capture_task(void *arg) {
    board_t *b = arg;
    audio_packet_t packet;
    memcpy(packet.header.magic, b->id ? "XVF2" : "XVF1", 4);
    uint32_t seq = 0;
    uint64_t delivered = 0;
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    while (1) {
        size_t bytes = 0;
        esp_err_t err = i2s_channel_read(b->rx, packet.words, sizeof(packet.words), &bytes, 500);
        if (err != ESP_OK || bytes == 0) {
            ESP_LOGW(TAG, "Board %d no I2S data", b->id);
            continue;
        }
        if (bytes % 8) {
            ESP_LOGE(TAG, "Board %d partial frame", b->id);
            continue;
        }

        uint32_t nonzero = 0, ones = 0, changing = 0;
        for (size_t k = 0; k < bytes / sizeof(packet.words[0]); k++) {
            nonzero += packet.words[k] != 0;
            ones += packet.words[k] == UINT32_MAX;
            if (k) changing += packet.words[k] != packet.words[k - 1];
        }
        b->nonzero_words += nonzero;
        b->ones_words += ones;
        b->changing_words += changing;
        packet.header.sequence = seq++;
        packet.header.delivered_frame = delivered;
        packet.header.frames = bytes / 8;
        packet.header.payload_bytes = bytes;
        packet.header.delivery_time_us = esp_timer_get_time();
        packet.header.rx_overflows = b->overflows;
        delivered += bytes / 8;
        b->frames += bytes / 8;

        // Keep DMA draining when the network falls behind. The next transmitted
        // sequence number still exposes any packet discarded here.
        if (subscription_snapshot(NULL, &packet.epoch) &&
            xQueueSend(tx_queue, &packet, 0) != pdPASS) {
            __atomic_fetch_add(&b->queue_drops, 1, __ATOMIC_RELAXED);
        }
    }
}

static void sender_task(void *arg) {
    audio_packet_t packet;
    struct {
        packet_header_t header;
        uint8_t payload[sizeof(packet.words)];
    } encoded;
    while (1) {
        if (xQueueReceive(tx_queue, &packet, portMAX_DELAY) != pdPASS) continue;
        if (__atomic_exchange_n(&tx_hold_requested, 0, __ATOMIC_RELAXED)) {
            vTaskDelay(pdMS_TO_TICKS(250));
        }

        struct sockaddr_in address;
        uint32_t epoch;
        if (!subscription_snapshot(&address, &epoch) || epoch != packet.epoch) continue;

        board_t *b = &boards[packet.header.magic[3] == '2'];
        const size_t bytes = packet.header.payload_bytes;
        const void *wire = &packet;
        size_t wire_bytes = sizeof(packet_header_t) + bytes;
        size_t packed = xvf_encode_words(packet.words, bytes / sizeof(packet.words[0]),
                                         encoded.payload, bytes);
        if (packed && packed < bytes) {
            encoded.header = packet.header;
            encoded.header.payload_bytes = 0x8000 | packed;
            wire = &encoded;
            wire_bytes = sizeof(packet_header_t) + packed;
        }

        // Limit the time spent retrying one datagram and discard stale backlog.
        const int64_t deadline = esp_timer_get_time() + TX_RETRY_US;
        unsigned attempts = 0;
        while (1) {
            if (!subscription_snapshot(NULL, &epoch) || epoch != packet.epoch) break;
            const int64_t now = esp_timer_get_time();
            if ((uint64_t)now - packet.header.delivery_time_us >= TX_MAX_AGE_US) {
                __atomic_fetch_add(&b->stale_drops, 1, __ATOMIC_RELAXED);
                break;
            }
            int sent = sendto(udp_sock, wire, wire_bytes, 0,
                              (struct sockaddr *)&address, sizeof(address));
            if (sent == (int)wire_bytes) break;
            int error = sent < 0 ? errno : EIO;
            if (attempts++ == 0) {
                __atomic_fetch_add(&b->send_errors, 1, __ATOMIC_RELAXED);
                __atomic_store_n(&b->last_send_errno, error, __ATOMIC_RELAXED);
            }
            bool retryable = error == ENOMEM || error == ENOBUFS || error == EAGAIN;
            if (!retryable || now >= deadline || attempts >= 3) {
                __atomic_fetch_add(&b->send_drops, 1, __ATOMIC_RELAXED);
                break;
            }
            vTaskDelay(1);
        }
    }
}


static void subscriber_task(void *arg) {
    udp_sock=socket(AF_INET,SOCK_DGRAM,IPPROTO_IP);
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(54322),.sin_addr.s_addr=htonl(INADDR_ANY)};
    if (udp_sock<0 || bind(udp_sock,(struct sockaddr*)&addr,sizeof(addr))) {ESP_LOGE(TAG,"UDP bind failed");vTaskDelete(NULL);return;}
    while (1) {
        char data[32];struct sockaddr_in source;socklen_t len=sizeof(source);
        int n=recvfrom(udp_sock,data,sizeof(data),0,(struct sockaddr*)&source,&len);
        if (n>=5 && !memcmp(data,"HELLO",5)) {
            taskENTER_CRITICAL(&subscriber_mux);
            bool changed = !subscribed || source.sin_addr.s_addr != subscriber.sin_addr.s_addr ||
                           source.sin_port != subscriber.sin_port;
            if (changed) subscriber_epoch++;
            subscriber = source;
            subscribed = true;
            taskEXIT_CRITICAL(&subscriber_mux);
            if (changed) ESP_LOGI(TAG, "Dual UDP receiver subscribed");
        } else if (n>=4 && !memcmp(data,"STOP",4)) {
            taskENTER_CRITICAL(&subscriber_mux);
            if (source.sin_addr.s_addr==subscriber.sin_addr.s_addr && source.sin_port==subscriber.sin_port) subscribed=false;
            taskEXIT_CRITICAL(&subscriber_mux);
        }
    }
}
static esp_err_t start_dual(bool require_control) {
    if (started) return ESP_ERR_INVALID_STATE;
    for (int i=0;i<2;i++) {
        esp_err_t err=init_control(&boards[i]);
        if (err!=ESP_OK) {
            ESP_LOGE(TAG,"Board %d control failed: %s",i,esp_err_to_name(err));
            if (require_control) return err;
        }
    }
    esp_err_t err=ESP_OK;int enabled=0;
    TaskHandle_t capture_handles[2] = {NULL, NULL};
    for (int i=0;i<2;i++) {
        board_t *b=&boards[i];i2s_chan_config_t ch=I2S_CHANNEL_DEFAULT_CONFIG(i,I2S_ROLE_SLAVE);
        ch.dma_desc_num=32;ch.dma_frame_num=128;
        err=i2s_new_channel(&ch,NULL,&b->rx);if (err!=ESP_OK) goto cleanup;
        i2s_std_config_t cfg={.clk_cfg=I2S_STD_CLK_DEFAULT_CONFIG(48000),
            .slot_cfg=I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,I2S_SLOT_MODE_STEREO),
            .gpio_cfg={.mclk=I2S_GPIO_UNUSED,.bclk=b->bclk,.ws=b->ws,.dout=I2S_GPIO_UNUSED,.din=b->din}};
        err=i2s_channel_init_std_mode(b->rx,&cfg);if (err!=ESP_OK) goto cleanup;
        i2s_event_callbacks_t cb={.on_recv_q_ovf=overflow_cb};
        err=i2s_channel_register_event_callback(b->rx,&cb,b);if (err!=ESP_OK) goto cleanup;
    }
    for (int i=0;i<2;i++) {err=i2s_channel_enable(boards[i].rx);if (err!=ESP_OK) goto cleanup;enabled++;}
    for (int i = 0; i < 2; i++) {
        if (xTaskCreatePinnedToCore(capture_task, i ? "audioB" : "audioA", 6144,
                                    &boards[i], 6, &capture_handles[i], 1) != pdPASS) {
            err = ESP_ERR_NO_MEM;
            goto cleanup;
        }
    }
    started = true;
    for (int i = 0; i < 2; i++) xTaskNotifyGive(capture_handles[i]);
    return ESP_OK;
cleanup:
    for (int i = 0; i < 2; i++) if (capture_handles[i]) vTaskDelete(capture_handles[i]);
    for (int i=0;i<enabled;i++) i2s_channel_disable(boards[i].rx);
    for (int i=0;i<2;i++) if (boards[i].rx) {i2s_del_channel(boards[i].rx);boards[i].rx=NULL;}
    return err;
}
static void wifi_event(void *unused, esp_event_base_t base, int32_t id, void *data) {
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = data;
        station_retries=0;
        ESP_LOGI(TAG,"Station IP: " IPSTR, IP2STR(&event->ip_info.ip));
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED && station_requested) {
        if (station_retries++ < 5) esp_wifi_connect();
        else ESP_LOGW(TAG,"Station connection failed");
    }
}

static int decode_hex(const char *hex, uint8_t *out, size_t capacity) {
    if (!strcmp(hex,"-")) return 0;
    size_t n=strlen(hex);
    if (n%2 || n/2>capacity) return -1;
    for (size_t i=0;i<n/2;i++) {
        char pair[3]={hex[2*i],hex[2*i+1],0};
        char *end; long byte=strtol(pair,&end,16);
        if (end != pair+2 || byte<0 || byte>255) return -1;
        out[i]=(uint8_t)byte;
    }
    return (int)(n/2);
}

static esp_err_t join_wifi(const char *ssid, const char *password) {
    wifi_config_t cfg={0};
    int ssid_n=decode_hex(ssid,cfg.sta.ssid,sizeof(cfg.sta.ssid));
    int pass_n=decode_hex(password,cfg.sta.password,sizeof(cfg.sta.password)-1);
    if (ssid_n<=0 || pass_n<0 || (pass_n>0 && pass_n<8)) return ESP_ERR_INVALID_ARG;
    station_requested=false;
    esp_wifi_disconnect();
    esp_err_t err=esp_wifi_set_config(WIFI_IF_STA,&cfg);
    if (err!=ESP_OK) return err;
    station_retries=0;station_requested=true;
    return esp_wifi_connect();
}

static void setup_wifi(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,wifi_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}


static void probe_main(void *unused) {
    esp_err_t err=nvs_flash_init();
    if (err==ESP_ERR_NVS_NO_FREE_PAGES || err==ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());err=nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    tx_queue = xQueueCreateStatic(TX_QUEUE_DEPTH, sizeof(audio_packet_t), tx_queue_storage, &tx_queue_state);
    if (!tx_queue || xTaskCreatePinnedToCore(sender_task, "udp_sender", 6144, NULL, 5, NULL, 1) != pdPASS) abort();
    setup_wifi();
    if (xTaskCreate(subscriber_task, "subscriber", 4096, NULL, 7, NULL) != pdPASS) abort();
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0,1024,0,0,NULL,0));
    ESP_LOGI(TAG,"Idle: START, PACK, MUTE board mic, STATUS, WIFI hex_ssid hex_password. No audio GPIOs at boot.");
    char line[1024];int used=0;
    while (1) {
        uint8_t ch;if (uart_read_bytes(UART_NUM_0,&ch,1,pdMS_TO_TICKS(100))<=0) continue;
        if (ch!='\n' && ch!='\r') {if (used<sizeof(line)-1) line[used++]=ch;continue;}
        if (!used) continue;
        line[used]=0;used=0;
        int board,mic,control_cmd,control_len;char ssid[65],password[127],dfu_hex[257];unsigned block;
        if (!strcmp(line,"DFUBEGIN") && !started) dfu_begin();
        else if (sscanf(line,"DFUBLOCK %u %256s",&block,dfu_hex)==2 && !started) dfu_block(block,dfu_hex);
        else if (!strcmp(line,"DFUEND") && !started) dfu_end();
        else if (!strcmp(line,"DFUREBOOT") && !started && dfu_finished) {
            uint8_t value=0;err=xvf_write(&boards[1],240,89,&value,1);
            ESP_LOGI(TAG,"DFU REBOOT result=%s",esp_err_to_name(err));
        }
        else if (!strcmp(line,"DFUSTATUS") && !started) dfu_status();
        else if (sscanf(line,"MICGATE %d %d",&board,&mic)==2 && board>=0 && board<2 && (mic==0 || mic==1)) {
            err=init_control(&boards[board]);
            if (err==ESP_OK) err=set_microphone_gate(&boards[board],mic);
            ESP_LOGI(TAG,"MICGATE B%d result=%s",board,esp_err_to_name(err));
        }
        else if (sscanf(line,"REBOOTBOARD %d",&board)==1 && !started && board>=0 && board<2) {
            uint8_t value=0;err=init_control(&boards[board]);
            if (boards[board].bus_ready) err=xvf_write(&boards[board],48,7,&value,1);
            boards[board].gate_initialized=false;
            ESP_LOGI(TAG,"REBOOTBOARD B%d request=%s (verify reboot separately)",board,esp_err_to_name(err));
        }
        else if (!strcmp(line,"CLOCK")) {
            clock_diagnostic();
            if (started) ESP_LOGW(TAG,"Clock diagnosis changed pin configuration; reset ESP before further capture.");
        }
        else if (sscanf(line,"READAUDIO %d %d %d",&board,&control_cmd,&control_len)==3 && board>=0 && board<2 && control_cmd>=0 && control_cmd<=31 && control_len>0 && control_len<=12) {
            uint8_t reply[12];err=init_control(&boards[board]);
            if (err==ESP_OK) err=xvf_read(&boards[board],35,control_cmd,reply,control_len);
            ESP_LOGI(TAG,"READAUDIO B%d c%d result=%s",board,control_cmd,esp_err_to_name(err));
            if (err==ESP_OK) ESP_LOG_BUFFER_HEX(TAG,reply,control_len);
        }
        else if (!strcmp(line,"CONTROL") && !started) {
            for (int i=0;i<2;i++) {
                err=init_control(&boards[i]);
                if (err==ESP_OK) err=configure_packed(&boards[i],-1);
                ESP_LOGI(TAG,"CONTROL board %d result: %s",i,esp_err_to_name(err));
            }
        }
        else if (!strcmp(line,"DIAG") && !started) diagnostic();
        else if (!strcmp(line,"SEPARATE") && !started) {
            boards[1].bclk=13;boards[1].ws=14;
            ESP_LOGI(TAG,"Diagnostic separate clocks selected: BCLK13 WS14 for B. Both ESP I2S controllers remain slave receivers.");
        }
        else if (!strcmp(line,"SWAPB") && !started) {
            board_t *b=&boards[1];if (b->bus_ready) {i2c_driver_delete(1);b->bus_ready=false;}
            int pin=b->sda;b->sda=b->scl;b->scl=pin;ESP_LOGI(TAG,"B control swapped: SDA%d SCL%d",b->sda,b->scl);
            diagnostic();
        }
        else if (!strcmp(line,"START")) ESP_LOGI(TAG,"START result: %s",esp_err_to_name(start_dual(true)));
        else if (!strcmp(line,"RAWSTART")) ESP_LOGI(TAG,"Diagnostic RAWSTART result: %s",esp_err_to_name(start_dual(false)));
        else if (!strcmp(line,"PACK") && started) {
            for (int i=0;i<2;i++) {err=configure_packed(&boards[i],-1);ESP_LOGI(TAG,"PACK board %d result: %s",i,esp_err_to_name(err));}
        } else if (sscanf(line,"MUTE %d %d",&board,&mic)==2 && started && board>=0 && board<2 && mic>=0 && mic<4) {
            err=configure_packed(&boards[board],mic);ESP_LOGI(TAG,"MUTE board %d mic %d result: %s",board,mic,esp_err_to_name(err));
        } else if (!strcmp(line,"STATUS")) {
            ESP_LOGI(TAG,"started=%d subscribed=%d",started,subscription_snapshot(NULL, NULL));
            ESP_LOGI(TAG,"heap_free=%lu heap_minimum=%lu",(unsigned long)esp_get_free_heap_size(),(unsigned long)esp_get_minimum_free_heap_size());
            for (int i=0;i<2;i++) ESP_LOGI(TAG,"Board %d frames=%lu overflows=%lu nonzero_words=%lu ones_words=%lu changing_words=%lu",i,(unsigned long)boards[i].frames,(unsigned long)boards[i].overflows,(unsigned long)boards[i].nonzero_words,(unsigned long)boards[i].ones_words,(unsigned long)boards[i].changing_words);
            for (int i = 0; i < 2; i++) {
                board_t *b = &boards[i];
                ESP_LOGI(TAG, "Board %d TX queue_drops=%lu stale_drops=%lu send_errors=%lu send_drops=%lu last_errno=%d",
                         i, (unsigned long)__atomic_load_n(&b->queue_drops, __ATOMIC_RELAXED),
                         (unsigned long)__atomic_load_n(&b->stale_drops, __ATOMIC_RELAXED),
                         (unsigned long)__atomic_load_n(&b->send_errors, __ATOMIC_RELAXED),
                         (unsigned long)__atomic_load_n(&b->send_drops, __ATOMIC_RELAXED),
                         __atomic_load_n(&b->last_send_errno, __ATOMIC_RELAXED));
            }
        } else if (!strcmp(line, "TXHOLD") && started && subscription_snapshot(NULL, NULL)) {
            __atomic_store_n(&tx_hold_requested, 1, __ATOMIC_RELAXED);
            ESP_LOGI(TAG, "Diagnostic UDP sender hold requested: 250 ms");
        } else if (!strcmp(line,"PAUSE")) {
            taskENTER_CRITICAL(&subscriber_mux);subscribed=false;taskEXIT_CRITICAL(&subscriber_mux);
            ESP_LOGI(TAG,"Network subscription paused; audio acquisition continues.");
        } else if (sscanf(line,"WIFI %64s %126s",ssid,password)==2) {
            err=join_wifi(ssid,password);memset(password,0,sizeof(password));memset(line,0,sizeof(line));
            ESP_LOGI(TAG,"WIFI result: %s",esp_err_to_name(err));
        } else ESP_LOGW(TAG,"Unknown command or audio not started");
    }
}
void app_main(void) {
    if (xTaskCreatePinnedToCore(probe_main,"control",8192,NULL,8,NULL,1)!=pdPASS) abort();
}
