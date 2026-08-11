/**
 * @file esp32p4_gui.c
 * @brief Responsive direct-rendered Specter Bootloader user interface.
 */

#include <inttypes.h>
#include <stddef.h>

#include "display_hal.h"
#include "esp32p4_platform.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "touch_hal.h"

#define COLOR_TEXT 0xf79eU
#define COLOR_TEXT_LOW 0x8452U
#define COLOR_BG 0x1926U
#define COLOR_CONTROL 0x4b0eU
#define COLOR_ACCENT 0x0397U
#define COLOR_INACTIVE 0x2945U
#define COLOR_ERROR_BG 0x8104U
#define COLOR_WARNING_BG 0x8a84U
#define COLOR_INFO_BG 0x29a9U

typedef enum gui_page {
  gui_page_none,
  gui_page_progress,
  gui_page_alert,
} gui_page_t;

static const char* TAG = "specter-ui";
static bool display_initialized;
static bool touch_initialized;
static gui_page_t displayed_page;

static bool init_if_needed(void) {
  if (display_initialized) {
    return true;
  }
  if (!specter_display_init()) {
    ESP_LOGE(TAG, "display initialization failed");
    return false;
  }
  display_initialized = true;
  bool ok = specter_display_fill(COLOR_BG);
  ok = specter_display_set_enabled(true) && ok;
  ok = specter_display_set_backlight(100) && ok;
  if (!ok) {
    ESP_LOGE(TAG, "display activation failed");
    specter_display_deinit();
    display_initialized = false;
    return false;
  }
  displayed_page = gui_page_none;
  return true;
}

static bool log_touch(void) {
  if (!touch_initialized) {
    return false;
  }
  specter_touch_point_t points[5];
  uint8_t count = 0;
  if (!specter_touch_read(points, 5, &count)) {
    return false;
  }
  for (uint8_t index = 0; index < count; ++index) {
    ESP_LOGI(TAG, "touch id=%u x=%u y=%u size=%u", points[index].id,
             points[index].x, points[index].y, points[index].size);
  }
  return true;
}

bool specter_esp32p4_gui_init(void) {
  display_initialized = false;
  touch_initialized = false;
  displayed_page = gui_page_none;
  return true;
}

void specter_esp32p4_gui_deinit(void) {
  if (touch_initialized) {
    (void)specter_touch_deinit();
    touch_initialized = false;
  }
  if (display_initialized) {
    specter_display_set_backlight(0);
    specter_display_set_enabled(false);
    specter_display_deinit();
  }
  display_initialized = false;
  displayed_page = gui_page_none;
}

static uint16_t alert_color(int type) {
  if (2 == type) {
    return COLOR_ERROR_BG;
  }
  if (1 == type) {
    return COLOR_WARNING_BG;
  }
  return COLOR_INFO_BG;
}

bool specter_esp32p4_gui_alert(int type, const char* caption, const char* text,
                               const char* user_action) {
  if (!caption || !text || !init_if_needed()) {
    ESP_LOGE(TAG, "unable to render alert");
    return false;
  }
  specter_display_size_t size = specter_display_size();
  uint16_t margin = size.width / 24U;
  uint16_t caption_height = size.height / 8U;
  uint16_t text_y = caption_height + size.height / 16U;
  uint16_t content_width = size.width - 2U * margin;
  bool ok = specter_display_fill_rect(0, 0, size.width, caption_height,
                                      alert_color(type));
  ok = specter_display_fill_rect(0, caption_height, size.width,
                                 size.height - caption_height, COLOR_BG) &&
       ok;
  ok = specter_display_draw_text(BL_FONT_NORMAL, 0, (caption_height - 20U) / 2U,
                                 size.width, caption, COLOR_TEXT,
                                 alert_color(type), true, false, NULL) &&
       ok;
  uint16_t final_y = text_y;
  ok = specter_display_draw_text(BL_FONT_NORMAL, margin, text_y, content_width,
                                 text, COLOR_TEXT, COLOR_BG, false, true,
                                 &final_y) &&
       ok;
  if (user_action) {
    uint16_t action_y = final_y + size.height / 16U;
    ok = specter_display_draw_text(BL_FONT_NORMAL, margin, action_y,
                                   content_width, user_action, COLOR_TEXT_LOW,
                                   COLOR_BG, false, true, NULL) &&
         ok;
  }
  ok = specter_display_flush(0, size.height) && ok;
  if (ok) {
    displayed_page = gui_page_alert;
  }
  ESP_LOGI(TAG, "alert rendered: %s (%ux%u, ok=%u)", caption, size.width,
           size.height, ok);
  return ok;
}

bool specter_esp32p4_gui_progress(const char* caption, const char* operation,
                                  uint32_t percent_x100) {
  if (!caption || !operation || !init_if_needed()) {
    ESP_LOGE(TAG, "unable to render progress");
    return false;
  }
  specter_display_size_t size = specter_display_size();
  uint16_t bar_width = (uint16_t)(size.width * 4U / 5U);
  uint16_t bar_height = size.height / 40U;
  if (bar_height < 12U) {
    bar_height = 12U;
  }
  uint16_t bar_x = (size.width - bar_width) / 2U;
  uint16_t bar_y = size.height * 5U / 16U;
  bool ok = true;
  if (displayed_page != gui_page_progress) {
    ok = specter_display_fill(COLOR_BG) && ok;
    ok = specter_display_draw_text(BL_FONT_NORMAL, 0, size.height / 16U,
                                   size.width, caption, COLOR_TEXT, COLOR_BG,
                                   true, false, NULL) &&
         ok;
  }
  ok = specter_display_draw_text(BL_FONT_NORMAL, bar_x, bar_y - 30U, bar_width,
                                 operation, COLOR_TEXT, COLOR_BG, false, false,
                                 NULL) &&
       ok;
  ok = specter_display_fill_rect(bar_x, bar_y, bar_width, bar_height,
                                 COLOR_CONTROL) &&
       ok;
  ok = specter_display_fill_rect(bar_x + 2U, bar_y + 2U, bar_width - 4U,
                                 bar_height - 4U, COLOR_INACTIVE) &&
       ok;
  uint32_t bounded = percent_x100 > 10000U ? 10000U : percent_x100;
  uint16_t active_width =
      (uint16_t)((uint32_t)(bar_width - 4U) * bounded / 10000U);
  if (active_width) {
    ok = specter_display_fill_rect(bar_x + 2U, bar_y + 2U, active_width,
                                   bar_height - 4U, COLOR_ACCENT) &&
         ok;
  }
  ok = specter_display_flush(bar_y - 30U, bar_height + 30U) && ok;
  if (ok) {
    displayed_page = gui_page_progress;
  }
  ESP_LOGI(TAG, "%s: %s (%" PRIu32 ".%02" PRIu32 "%%)", caption, operation,
           bounded / 100U, bounded % 100U);
  return ok;
}

bool specter_esp32p4_gui_hardware_diagnostic(void) {
  ESP_LOGW(TAG, "starting development-only UI hardware diagnostic");
  const uint8_t brightness[] = {25, 50, 100};
  for (size_t index = 0; index < sizeof(brightness); ++index) {
    if (!specter_esp32p4_gui_progress(
            "Display Test", "Rendering progress",
            (uint32_t)(index + 1U) * 10000U / sizeof(brightness)) ||
        !specter_display_set_backlight(brightness[index])) {
      goto failed;
    }
    vTaskDelay(pdMS_TO_TICKS(300));
  }
  if (!specter_esp32p4_gui_alert(
          0, "Touch Test",
          "Touch center and separated corners to log coordinates.", NULL)) {
    goto failed;
  }
  touch_initialized = specter_touch_init();
  if (!touch_initialized) {
    ESP_LOGE(TAG, "touch diagnostic initialization failed");
    goto failed;
  }
  for (uint32_t elapsed_ms = 0; elapsed_ms < 30000U; elapsed_ms += 20U) {
    if (!log_touch()) {
      ESP_LOGE(TAG, "touch diagnostic read failed");
      goto failed;
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
  if (!specter_touch_deinit()) {
    touch_initialized = false;
    ESP_LOGE(TAG, "touch diagnostic cleanup failed");
    goto failed;
  }
  touch_initialized = false;
  ESP_LOGW(TAG, "UI hardware diagnostic succeeded");
  return true;

failed:
  if (touch_initialized) {
    if (!specter_touch_deinit()) {
      ESP_LOGE(TAG, "touch cleanup after diagnostic failure failed");
    }
    touch_initialized = false;
  }
  ESP_LOGE(TAG, "UI hardware diagnostic failed");
  return false;
}
