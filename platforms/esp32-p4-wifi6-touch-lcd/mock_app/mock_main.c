/**
 * @file mock_main.c
 * @brief Entry point for the ESP32-P4 mock Main Firmware.
 */

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mock_screen.h"
#include "mock_status.h"
#include "mock_telemetry.h"

#ifndef SPECTER_MOCK_VERSION_TAG
#define SPECTER_MOCK_VERSION_TAG "0100000099"
#endif

static const char version_tag[] __attribute__((used)) =
    "<version:tag10>" SPECTER_MOCK_VERSION_TAG "</version:tag10>";

void app_main(void) {
  __asm__ volatile("" : : "r"(version_tag));
  specter_mock_status_t status;
  specter_mock_status_collect(&status);
  status.telemetry_ready = specter_mock_telemetry_init();
  status.telemetry_error = status.telemetry_ready ? 0 : 1;
  /* Emit a diagnostic snapshot before the synchronous panel bring-up. The
   * first complete acceptance record is still emitted after rendering. */
  if (status.telemetry_ready) {
    specter_mock_telemetry_emit(&status, 0U,
                                (uint64_t)esp_timer_get_time() / 1000U);
  }
  status.ui_ready = specter_mock_screen_render(&status);
  status.ui_error = status.ui_ready ? 0 : 1;

  /* Framing tolerates earlier ROM/Root Loader text. From here on, the mock
   * application owns the telemetry stream and emits no text logs. */
  esp_log_level_set("*", ESP_LOG_NONE);
  uint32_t sequence = 1U;
  for (;;) {
    uint64_t uptime_ms = (uint64_t)esp_timer_get_time() / 1000U;
    if (!specter_mock_telemetry_emit(&status, sequence, uptime_ms)) {
      status.telemetry_ready = false;
      status.telemetry_error = 2;
    }
    ++sequence;
    vTaskDelay(pdMS_TO_TICKS(5000U));
  }
}
