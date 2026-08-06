/**
 * @file esp32p4_gui.c
 * @brief Board-independent log forwarding until Phase 6 display integration.
 */

#include <inttypes.h>

#include "esp32p4_platform.h"
#include "esp_log.h"

static const char* TAG = "specter-ui";

bool specter_esp32p4_gui_init(void) { return true; }

void specter_esp32p4_gui_deinit(void) {}

void specter_esp32p4_gui_alert(int type, const char* caption,
                               const char* text) {
  if (2 == type) {
    ESP_LOGE(TAG, "%s: %s", caption, text);
  } else if (1 == type) {
    ESP_LOGW(TAG, "%s: %s", caption, text);
  } else {
    ESP_LOGI(TAG, "%s: %s", caption, text);
  }
}

void specter_esp32p4_gui_progress(const char* caption, const char* operation,
                                  uint32_t percent_x100) {
  ESP_LOGI(TAG, "%s: %s (%" PRIu32 ".%02" PRIu32 "%%)", caption, operation,
           percent_x100 / 100U, percent_x100 % 100U);
}
