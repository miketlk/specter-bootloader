/**
 * @file touch_hal.h
 * @brief Narrow GT911 touch interface shared by both Waveshare boards.
 */

#ifndef SPECTER_ESP32P4_TOUCH_HAL_H_INCLUDED
#define SPECTER_ESP32P4_TOUCH_HAL_H_INCLUDED

#include <stdbool.h>
#include <stdint.h>

#include "touch_decode.h"

bool specter_touch_init(void);
bool specter_touch_deinit(void);
bool specter_touch_read(specter_touch_point_t* points, uint8_t capacity,
                        uint8_t* count);

#endif  // SPECTER_ESP32P4_TOUCH_HAL_H_INCLUDED
