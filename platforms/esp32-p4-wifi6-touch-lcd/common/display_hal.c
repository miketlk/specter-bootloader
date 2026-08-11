/**
 * @file display_hal.c
 * @brief Direct RGB565 drawing for the ESP32-P4 bootloader UI.
 */

#include "display_hal.h"

#include <stddef.h>
#include <string.h>

#include "esp32p4_platform.h"
#include "fonts.h"

static uint16_t* framebuffer;
static specter_display_size_t size;
static bool initialized;

bool specter_display_init(void) {
  if (initialized) {
    return true;
  }
  if (!specter_esp32p4_board_display_init() ||
      !specter_esp32p4_board_framebuffer(&framebuffer, &size.width,
                                         &size.height)) {
    specter_esp32p4_board_display_deinit();
    framebuffer = NULL;
    memset(&size, 0, sizeof(size));
    return false;
  }
  initialized = true;
  return true;
}

void specter_display_deinit(void) {
  if (initialized) {
    specter_esp32p4_board_display_deinit();
  }
  initialized = false;
  framebuffer = NULL;
  memset(&size, 0, sizeof(size));
}

specter_display_size_t specter_display_size(void) { return size; }

bool specter_display_flush(uint16_t y, uint16_t height) {
  return initialized && y < size.height && height <= size.height - y &&
         specter_esp32p4_board_flush(y, height);
}

bool specter_display_fill_rect(uint16_t x, uint16_t y, uint16_t width,
                               uint16_t height, uint16_t color) {
  if (!initialized || !width || !height || x >= size.width ||
      y >= size.height) {
    return false;
  }
  if (width > size.width - x) {
    width = size.width - x;
  }
  if (height > size.height - y) {
    height = size.height - y;
  }
  for (uint16_t row = 0; row < height; ++row) {
    uint16_t* pixel = framebuffer + (size_t)(y + row) * size.width + x;
    for (uint16_t column = 0; column < width; ++column) {
      pixel[column] = color;
    }
  }
  return true;
}

bool specter_display_fill(uint16_t color) {
  return specter_display_fill_rect(0, 0, size.width, size.height, color) &&
         specter_display_flush(0, size.height);
}

static void draw_character(uint16_t x, uint16_t y, char character,
                           uint16_t color, uint16_t background,
                           const sFONT* font) {
  if (character < ' ' || character > '~') {
    character = '?';
  }
  const uint16_t bytes_per_row = (font->Width + 7U) / 8U;
  const uint8_t* glyph =
      font->table + (size_t)(character - ' ') * bytes_per_row * font->Height;
  for (uint16_t row = 0; row < font->Height; ++row) {
    for (uint16_t column = 0; column < font->Width; ++column) {
      uint8_t bits = glyph[(size_t)row * bytes_per_row + column / 8U];
      uint8_t mask = (uint8_t)(0x80U >> (column % 8U));
      framebuffer[(size_t)(y + row) * size.width + x + column] =
          (bits & mask) ? color : background;
    }
  }
}

static size_t line_length(const char* text, size_t maximum, bool multiline) {
  size_t length = 0;
  size_t word_boundary = 0;
  while (text[length] && text[length] != '\n' && text[length] != '\r' &&
         length < maximum) {
    if (' ' == text[length]) {
      word_boundary = length;
    }
    ++length;
  }
  if (multiline && length == maximum && text[length] && word_boundary) {
    return word_boundary;
  }
  return length;
}

static bool draw_text(uint16_t x, uint16_t y, uint16_t width, const char* text,
                      uint16_t color, uint16_t background, bool centered,
                      bool multiline, uint16_t* final_y, const sFONT* font) {
  if (!initialized || !text || x >= size.width || y >= size.height ||
      width > size.width - x || width < font->Width) {
    return false;
  }
  size_t maximum = width / font->Width;
  uint16_t current_y = y;
  const char* cursor = text;
  do {
    if (current_y > size.height - font->Height) {
      return false;
    }
    size_t length = line_length(cursor, maximum, multiline);
    if (!multiline && cursor[length] && cursor[length] != '\n' &&
        cursor[length] != '\r') {
      return false;
    }
    uint16_t line_x = x;
    if (centered && length < maximum) {
      line_x += (uint16_t)((width - length * font->Width) / 2U);
    }
    specter_display_fill_rect(x, current_y, width, font->Height, background);
    for (size_t index = 0; index < length; ++index) {
      draw_character((uint16_t)(line_x + index * font->Width), current_y,
                     cursor[index], color, background, font);
    }
    if (final_y) {
      *final_y = current_y;
    }
    cursor += length;
    while (' ' == *cursor || '\t' == *cursor || '\r' == *cursor) {
      ++cursor;
    }
    if ('\n' == *cursor) {
      ++cursor;
    }
    current_y += font->Height;
  } while (multiline && *cursor);
  return specter_display_flush(y, (uint16_t)(current_y - y));
}

static const sFONT* lookup_font(bl_font_t font) {
  switch (font) {
    case BL_FONT_NORMAL:
      return &Font20;
    case BL_FONT_SMALL:
      return &Font12;
    default:
      return NULL;
  }
}

bool specter_display_draw_text(bl_font_t font, uint16_t x, uint16_t y,
                               uint16_t width, const char* text, uint16_t color,
                               uint16_t background, bool centered,
                               bool multiline, uint16_t* final_y) {
  const sFONT* selected_font = lookup_font(font);
  if (!selected_font) {
    return false;
  }
  return draw_text(x, y, width, text, color, background, centered, multiline,
                   final_y, selected_font);
}

bool specter_display_set_backlight(uint8_t percent) {
  return initialized && specter_esp32p4_board_backlight(percent);
}

bool specter_display_set_enabled(bool enabled) {
  return initialized && specter_esp32p4_board_display_enabled(enabled);
}
