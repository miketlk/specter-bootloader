/**
 * @file display_hal.h
 * @brief Board-independent RGB565 display primitives.
 */

#ifndef SPECTER_ESP32P4_DISPLAY_HAL_H_INCLUDED
#define SPECTER_ESP32P4_DISPLAY_HAL_H_INCLUDED

#include <stdbool.h>
#include <stdint.h>

typedef struct specter_display_size {
  uint16_t width;
  uint16_t height;
} specter_display_size_t;

typedef enum bl_font {
  BL_FONT_NORMAL,
  BL_FONT_SMALL,
} bl_font_t;

bool specter_display_init(void);
void specter_display_deinit(void);
specter_display_size_t specter_display_size(void);
bool specter_display_fill(uint16_t color);
bool specter_display_fill_rect(uint16_t x, uint16_t y, uint16_t width,
                               uint16_t height, uint16_t color);
bool specter_display_draw_text(bl_font_t font, uint16_t x, uint16_t y,
                               uint16_t width, const char* text, uint16_t color,
                               uint16_t background, bool centered,
                               bool multiline, uint16_t* final_y);
bool specter_display_flush(uint16_t y, uint16_t height);
bool specter_display_set_backlight(uint8_t percent);
bool specter_display_set_enabled(bool enabled);

#endif  // SPECTER_ESP32P4_DISPLAY_HAL_H_INCLUDED
