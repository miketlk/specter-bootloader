/**
 * @file esp32p4_reset.h
 * @brief Reset-retained Root Loader request shared ABI.
 */

#ifndef ESP32P4_RESET_H_INCLUDED
#define ESP32P4_RESET_H_INCLUDED

#include <stdbool.h>
#include <stdint.h>

#define SPECTER_RTC_REQUEST_MAGIC 0x51525053U
#define SPECTER_RTC_REQUEST_REVISION 1U

typedef enum specter_rtc_command {
  specter_rtc_boot_main = 1,
  specter_rtc_confirm_bootloader = 2,
} specter_rtc_command_t;

typedef struct __attribute__((packed)) specter_rtc_request {
  uint32_t magic;
  uint32_t revision;
  uint32_t command;
  uint32_t target;
  uint32_t sequence;
  uint32_t argument;
  uint32_t crc;
} specter_rtc_request_t;

/// Writes a CRC-protected request into ESP-IDF's custom retained RTC area.
bool specter_esp32p4_reset_request_write(specter_rtc_command_t command,
                                         uint32_t target, uint32_t sequence,
                                         uint32_t argument);

#endif  // ESP32P4_RESET_H_INCLUDED
