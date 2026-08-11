/**
 * @file esp32p4_board_stub.c
 * @brief Board seam used until a selected board has hardware support.
 */

#include "esp32p4_platform.h"

bool specter_esp32p4_board_display_init(void) { return false; }

void specter_esp32p4_board_display_deinit(void) {}

bool specter_esp32p4_board_framebuffer(uint16_t** framebuffer,
                                      uint16_t* width, uint16_t* height) {
  (void)framebuffer;
  (void)width;
  (void)height;
  return false;
}

bool specter_esp32p4_board_backlight(uint8_t percent) {
  (void)percent;
  return false;
}

bool specter_esp32p4_board_display_enabled(bool enabled) {
  (void)enabled;
  return false;
}

bool specter_esp32p4_board_flush(uint16_t y, uint16_t height) {
  (void)y;
  (void)height;
  return false;
}
