/**
 * @file esp32p4_reset.h
 * @brief Reset-retained Root Loader request shared ABI.
 */

#ifndef ESP32P4_RESET_H_INCLUDED
#define ESP32P4_RESET_H_INCLUDED

#include <stdbool.h>
#include <stdint.h>

#include "esp32p4_boot_contract.h"

/// Writes a CRC-protected request into ESP-IDF's custom retained RTC area.
bool specter_esp32p4_reset_request_write(specter_rtc_command_t command,
                                         uint32_t target, uint32_t sequence,
                                         uint32_t argument);

#endif  // ESP32P4_RESET_H_INCLUDED
