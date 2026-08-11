/**
 * @file bl_syscalls_esp32p4.c
 * @brief Shared ESP32-P4 platform lifecycle and user feedback syscalls.
 */

#include <inttypes.h>

#include "bl_syscalls.h"
#include "esp32p4_platform.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "ui_prompts.h"

static const char* TAG = "specter-platform";
#if CONFIG_SPECTER_UI_HARDWARE_DIAGNOSTIC
static bool ui_diagnostic_ran = false;
#endif

const char* specter_esp32p4_platform_id(void) {
#if CONFIG_SPECTER_BOARD_LCD_4P3
  return "esp32-p4-wifi6-touch-lcd-4p3";
#elif CONFIG_SPECTER_BOARD_LCD_5
  return "esp32-p4-wifi6-touch-lcd-5";
#else
#error "A supported Waveshare ESP32-P4 board must be selected"
#endif
}

const char* blsys_platform_id(void) { return specter_esp32p4_platform_id(); }

bool blsys_init(void) {
  esp_err_t nvs_result = nvs_flash_init();
  if (ESP_OK != nvs_result) {
    ESP_LOGE(TAG, "NVS initialization failed without destructive recovery: %s",
             esp_err_to_name(nvs_result));
    return false;
  }
  if (!specter_esp32p4_flash_map_init()) {
    nvs_flash_deinit();
    return false;
  }
  if (!specter_esp32p4_gui_init()) {
    nvs_flash_deinit();
    return false;
  }
  ESP_LOGI(TAG, "initialized %s without network companion components",
           blsys_platform_id());
#if CONFIG_SPECTER_UI_HARDWARE_DIAGNOSTIC
  if (!ui_diagnostic_ran) {
    ui_diagnostic_ran = true;
    if (!specter_esp32p4_gui_hardware_diagnostic()) {
      ESP_LOGE(TAG, "UI hardware diagnostic failed during initialization");
      specter_esp32p4_gui_deinit();
      nvs_flash_deinit();
      return false;
    }
  }
#endif
  return true;
}

void blsys_deinit(void) {
  blsys_media_umount();
  specter_esp32p4_gui_deinit();
  nvs_flash_deinit();
}

void blsys_fatal_error(const char* text) {
  specter_esp32p4_gui_alert(bl_alert_error, "Bootloader Error",
                            text ? text : "Internal error",
                            specter_esp32p4_reset_prompt(true, BL_FOREVER));
  ESP_LOGE(TAG, "fatal error: %s", text ? text : "Internal error");
  blsys_media_umount();
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(1000U));
  }
}

bl_alert_status_t blsys_alert(blsys_alert_type_t type, const char* caption,
                              const char* text, uint32_t time_ms,
                              uint32_t flags) {
  if ((int)type < 0 || type >= bl_nalerts || !caption || !text || flags) {
    blsys_fatal_error("Invalid alert arguments");
  }
  if (!specter_esp32p4_gui_alert(
          type, caption, text,
          specter_esp32p4_reset_prompt(false, time_ms))) {
    blsys_fatal_error("Unable to display alert");
  }
  if (BL_FOREVER == time_ms) {
    blsys_media_umount();
    for (;;) {
      vTaskDelay(pdMS_TO_TICKS(1000U));
    }
  }
  if (time_ms) {
    vTaskDelay(pdMS_TO_TICKS(time_ms));
  }
  return bl_alert_terminated;
}

void blsys_progress(const char* caption, const char* operation,
                    uint32_t percent_x100) {
  if (!specter_esp32p4_gui_progress(
          caption, operation,
          percent_x100 > 10000U ? 10000U : percent_x100)) {
    blsys_fatal_error("Unable to update progress display");
  }
}
