/**
 * @file esp32p4_reset.c
 * @brief Reset-retained Root Loader request and firmware handoff.
 */

#include "esp32p4_reset.h"

#include <stddef.h>
#include <string.h>

#include "bl_syscalls.h"
#include "bootloader_common.h"
#include "crc32.h"
#include "esp32p4_platform.h"
#include "esp32p4_trailer.h"
#include "esp_image_format.h"
#include "esp_system.h"
#include "sdkconfig.h"

_Static_assert(CONFIG_BOOTLOADER_CUSTOM_RESERVE_RTC_SIZE >=
                   sizeof(specter_rtc_request_t),
               "custom retained RTC area is too small for Specter request");

bool specter_esp32p4_reset_request_write(specter_rtc_command_t command,
                                         uint32_t target, uint32_t sequence,
                                         uint32_t argument) {
  if ((command != specter_rtc_boot_main &&
       command != specter_rtc_confirm_bootloader) ||
      !sequence) {
    return false;
  }
  rtc_retain_mem_t* retained = bootloader_common_get_rtc_retain_mem();
  if (!retained) {
    return false;
  }
  specter_rtc_request_t request = {
      .magic = SPECTER_RTC_REQUEST_MAGIC,
      .revision = SPECTER_RTC_REQUEST_REVISION,
      .command = command,
      .target = target,
      .sequence = sequence,
      .argument = argument,
  };
  request.crc = crc32_fast(&request, offsetof(specter_rtc_request_t, crc), 0U);
  memset(retained->custom, 0, sizeof(retained->custom));
  memcpy(retained->custom, &request, sizeof(request));
  return true;
}

bool blsys_start_firmware(bl_addr_t start_addr, uint32_t argument) {
  const esp_partition_t* main = specter_esp32p4_partition(specter_role_main);
  specter_approval_record_t approval;
  if (!main || start_addr != main->address ||
      !specter_esp32p4_approval_read(specter_role_main, &approval, true) ||
      !specter_esp32p4_reset_request_write(specter_rtc_boot_main,
                                           specter_role_main, approval.sequence,
                                           argument)) {
    return false;
  }
  esp_restart();
  return true;
}
