/**
 * @file touch_decode.c
 * @brief Pure GT911 point-record decoder.
 */

#include "touch_decode.h"

bool specter_touch_decode_records(const uint8_t* raw, uint8_t available,
                                  specter_touch_point_t* points,
                                  uint8_t capacity, uint16_t width,
                                  uint16_t height, uint8_t* count) {
  if (!count || available > SPECTER_TOUCH_GT911_MAX_POINTS ||
      (available && (!raw || !points || !capacity)) || !width || !height) {
    return false;
  }
  *count = 0;
  uint8_t returned = available < capacity ? available : capacity;
  for (uint8_t index = 0; index < returned; ++index) {
    const uint8_t* point =
        raw + (size_t)index * SPECTER_TOUCH_GT911_POINT_BYTES;
    specter_touch_point_t decoded = {
        .x = (uint16_t)(point[1] | ((uint16_t)point[2] << 8U)),
        .y = (uint16_t)(point[3] | ((uint16_t)point[4] << 8U)),
        .size = (uint16_t)(point[5] | ((uint16_t)point[6] << 8U)),
        .id = point[0] & 0x0fU,
    };
    if (decoded.x >= width || decoded.y >= height) {
      return false;
    }
    points[index] = decoded;
  }
  *count = returned;
  return true;
}
