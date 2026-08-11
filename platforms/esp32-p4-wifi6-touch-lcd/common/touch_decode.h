/**
 * @file touch_decode.h
 * @brief Pure GT911 point-record decoding shared by firmware and host tests.
 */

#ifndef SPECTER_ESP32P4_TOUCH_DECODE_H_INCLUDED
#define SPECTER_ESP32P4_TOUCH_DECODE_H_INCLUDED

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SPECTER_TOUCH_GT911_POINT_BYTES 8U
#define SPECTER_TOUCH_GT911_MAX_POINTS 5U

typedef struct specter_touch_point {
  uint16_t x;
  uint16_t y;
  uint16_t size;
  uint8_t id;
} specter_touch_point_t;

/** Decodes complete eight-byte GT911 records starting at register 0x814f. */
bool specter_touch_decode_records(const uint8_t* raw, uint8_t available,
                                  specter_touch_point_t* points,
                                  uint8_t capacity, uint16_t width,
                                  uint16_t height, uint8_t* count);

#endif  // SPECTER_ESP32P4_TOUCH_DECODE_H_INCLUDED
