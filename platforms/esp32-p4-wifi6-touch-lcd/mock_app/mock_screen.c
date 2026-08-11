/**
 * @file mock_screen.c
 * @brief One-page status renderer for the mock Main Firmware.
 */

#include "mock_screen.h"

#include <inttypes.h>
#include <stdio.h>

#include "display_hal.h"

#define COLOR_BACKGROUND 0x0841U
#define COLOR_PANEL 0x1082U
#define COLOR_TEXT 0xffffU
#define COLOR_MUTED 0xbdf7U
#define COLOR_READY 0x07e0U
#define COLOR_FAIL 0xf800U

static bool line(uint16_t x, uint16_t* y, uint16_t width, const char* text,
                 uint16_t color, bool centered) {
  uint16_t final_y = *y;
  bool result =
      specter_display_draw_text(BL_FONT_NORMAL, x, *y, width, text, color,
                                COLOR_PANEL, centered, false, &final_y);
  *y = (uint16_t)(final_y + 28U);
  return result;
}

static bool small_line(uint16_t x, uint16_t* y, uint16_t width,
                       const char* text, uint16_t color) {
  uint16_t final_y = *y;
  bool result =
      specter_display_draw_text(BL_FONT_SMALL, x, *y, width, text, color,
                                COLOR_PANEL, false, false, &final_y);
  *y = (uint16_t)(final_y + 17U);
  return result;
}

static bool field(uint16_t x, uint16_t* y, uint16_t width, const char* label,
                  const char* value) {
  bool ok = small_line(x, y, width, label, COLOR_MUTED);
  ok = small_line(x, y, width, value, COLOR_TEXT) && ok;
  *y += 7U;
  return ok;
}

bool specter_mock_screen_render(const specter_mock_status_t* status) {
  if (!status || !specter_display_init()) {
    return false;
  }
  specter_display_size_t size = specter_display_size();
  const uint16_t margin = size.width >= 700U ? 52U : 24U;
  const uint16_t width = (uint16_t)(size.width - 2U * margin);
  const bool ready = status->main_role_check == specter_mock_check_pass &&
                     status->image_check == specter_mock_check_pass &&
                     status->bloat_check == specter_mock_check_pass &&
                     status->telemetry_ready;
  if (!specter_display_fill(COLOR_BACKGROUND) ||
      !specter_display_fill_rect(margin, 44U, width,
                                 (uint16_t)(size.height - 88U), COLOR_PANEL)) {
    return false;
  }

  uint16_t y = size.height >= 1000U ? 96U : 76U;
  char text[96];
  bool ok = line(margin, &y, width, SPECTER_MOCK_NAME, COLOR_TEXT, true);
  ok = line(margin, &y, width, ready ? "READY" : "CHECK FAILED",
            ready ? COLOR_READY : COLOR_FAIL, true) &&
       ok;
  y += 18U;
  snprintf(text, sizeof(text), "%s / %ux%u", status->board_profile,
           (unsigned)status->display_width, (unsigned)status->display_height);
  ok = field(margin, &y, width, "BOARD / DISPLAY", text) && ok;
  ok = field(margin, &y, width, "VERSION", status->project_version) && ok;
  snprintf(text, sizeof(text), "%" PRIu32 " / %" PRIu32 " bytes",
           status->requested_bloat, status->linked_bloat);
  ok = field(margin, &y, width, "BLOAT REQUESTED / LINKED", text) && ok;
  snprintf(text, sizeof(text), "%" PRIu32 " bytes / %" PRIu32 " free",
           status->image_length, status->remaining_bytes);
  ok = field(margin, &y, width, "IMAGE", text) && ok;
  snprintf(text, sizeof(text), "0x%08" PRIx32 " / %" PRIu32 " bytes",
           status->partition_address, status->partition_size);
  ok = field(margin, &y, width, "MAIN PARTITION", text) && ok;
  snprintf(text, sizeof(text), "role %s / image %s / bloat %s",
           specter_mock_check_text(status->main_role_check),
           specter_mock_check_text(status->image_check),
           specter_mock_check_text(status->bloat_check));
  ok = field(margin, &y, width, "CHECKS", text) && ok;
  snprintf(text, sizeof(text), "%s / digest %s",
           specter_mock_approval_state_text(status->approval_state),
           specter_mock_check_text(status->approval_digest_check));
  ok = field(margin, &y, width, "APPROVAL", text) && ok;
  snprintf(text, sizeof(text), "USB TO UART / %s",
           status->telemetry_ready ? "ready" : "fail");
  ok = field(margin, &y, width, "TELEMETRY", text) && ok;
  /* Text flushes only its own scanline bands. Commit the untouched panel and
   * background rows too; otherwise cached RGB565 writes appear as broad
   * bands and stale horizontal lines on the continuously scanned DPI panel. */
  return specter_display_flush(0, size.height) &&
         specter_display_set_enabled(true) &&
         specter_display_set_backlight(80U) && ok;
}
