#include "esp32p4_gui_test_backend.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "display_hal.h"
#include "touch_hal.h"

static unsigned calls[esp32p4_test_operation_count];
static unsigned display_deinits;
static esp32p4_test_operation_t failed_operation;
static unsigned failed_occurrence;

static bool operation_result(esp32p4_test_operation_t operation) {
  ++calls[operation];
  return operation != failed_operation || calls[operation] != failed_occurrence;
}

void esp32p4_test_backend_reset(void) {
  memset(calls, 0, sizeof(calls));
  display_deinits = 0;
  failed_operation = esp32p4_test_operation_count;
  failed_occurrence = 0;
}

void esp32p4_test_backend_fail(esp32p4_test_operation_t operation,
                               unsigned occurrence) {
  failed_operation = operation;
  failed_occurrence = occurrence;
}

void esp32p4_test_backend_clear_failure(void) {
  failed_operation = esp32p4_test_operation_count;
  failed_occurrence = 0;
}

unsigned esp32p4_test_backend_calls(esp32p4_test_operation_t operation) {
  return calls[operation];
}

unsigned esp32p4_test_backend_display_deinits(void) { return display_deinits; }

bool specter_display_init(void) {
  return operation_result(esp32p4_test_display_init);
}

void specter_display_deinit(void) { ++display_deinits; }

specter_display_size_t specter_display_size(void) {
  specter_display_size_t size = {.width = 800, .height = 480};
  return size;
}

bool specter_display_fill(uint16_t color) {
  (void)color;
  return operation_result(esp32p4_test_display_fill);
}

bool specter_display_fill_rect(uint16_t x, uint16_t y, uint16_t width,
                               uint16_t height, uint16_t color) {
  (void)x;
  (void)y;
  (void)width;
  (void)height;
  (void)color;
  return operation_result(esp32p4_test_display_fill_rect);
}

bool specter_display_draw_text(bl_font_t font, uint16_t x, uint16_t y,
                               uint16_t width, const char* text, uint16_t color,
                               uint16_t background, bool centered,
                               bool multiline, uint16_t* final_y) {
  (void)font;
  (void)x;
  (void)width;
  (void)text;
  (void)color;
  (void)background;
  (void)centered;
  (void)multiline;
  if (final_y) {
    *final_y = y;
  }
  return operation_result(esp32p4_test_display_draw_text);
}

bool specter_display_flush(uint16_t y, uint16_t height) {
  (void)y;
  (void)height;
  return operation_result(esp32p4_test_display_flush);
}

bool specter_display_set_backlight(uint8_t percent) {
  (void)percent;
  return operation_result(esp32p4_test_display_backlight);
}

bool specter_display_set_enabled(bool enabled) {
  (void)enabled;
  return operation_result(esp32p4_test_display_enabled);
}

bool specter_touch_init(void) {
  return operation_result(esp32p4_test_touch_init);
}

bool specter_touch_deinit(void) {
  return operation_result(esp32p4_test_touch_deinit);
}

bool specter_touch_read(specter_touch_point_t* points, uint8_t capacity,
                        uint8_t* count) {
  (void)points;
  (void)capacity;
  if (count) {
    *count = 0;
  }
  return operation_result(esp32p4_test_touch_read);
}
