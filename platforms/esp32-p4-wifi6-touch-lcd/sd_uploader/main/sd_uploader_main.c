/** @file sd_uploader_main.c
 * @brief RAM-only UART service; no automatic reset or flash initialization.
 */
#include "board.h"
#include "driver/uart.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "protocol.h"
void sdu_session_init(void);
void sdu_session_tick(void);
void sdu_dispatch(const uint8_t* data, size_t length);
void sdu_reservations_link(void);
uint64_t sdu_frame_cpu_us;
void app_main(void) {
  sdu_reservations_link();
  uart_config_t config = {.baud_rate = CONFIG_SDU_UART_BAUD,
                          .data_bits = UART_DATA_8_BITS,
                          .parity = UART_PARITY_DISABLE,
                          .stop_bits = UART_STOP_BITS_1,
                          .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
                          .source_clk = UART_SCLK_DEFAULT};
  ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &config));
  ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0, SPECTER_UART_TX_GPIO,
                               SPECTER_UART_RX_GPIO, UART_PIN_NO_CHANGE,
                               UART_PIN_NO_CHANGE));
  ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 32768, 0, 0, NULL, 0));
  sdu_session_init();
  uint8_t buffer[256];
  uint64_t feed_us = 0;
  while (1) {
    size_t pending = 0;
    ESP_ERROR_CHECK(uart_get_buffered_data_len(UART_NUM_0, &pending));
    size_t wanted = pending ? pending : 1;
    if (wanted > sizeof(buffer)) wanted = sizeof(buffer);
    int n = uart_read_bytes(UART_NUM_0, buffer, wanted, pdMS_TO_TICKS(50));
    int64_t batch = esp_timer_get_time();
    for (int i = 0; i < n; ++i) {
      const uint8_t* payload;
      size_t length;
      if (sdu_feed(buffer[i], batch, &payload, &length)) {
        feed_us += esp_timer_get_time() - batch;
        sdu_frame_cpu_us = feed_us;
        feed_us = 0;
        sdu_dispatch(payload, length);
        batch = esp_timer_get_time();
      }
    }
    if (n > 0) feed_us += esp_timer_get_time() - batch;
    sdu_session_tick();
  }
}
