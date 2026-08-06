/**
 * @file main.c
 * @brief ESP-IDF application wrapper for the shared Specter Bootloader core.
 */

#include <string.h>

#include "bl_integrity_check.h"
#include "bl_syscalls.h"
#include "bl_util.h"
#include "bootloader.h"
#include "crc32.h"
#include "esp32p4_platform.h"
#include "esp_ota_ops.h"

static const char version_tag[] BL_ATTRS((used)) =
    "<version:tag10>0100000299</version:tag10>";

void app_main(void) {
  bl_keep_variable(&version_tag);

  const esp_partition_t* running = esp_ota_get_running_partition();
  bool valid_boot_role =
      running && ((running->address == SPECTER_BOOT_A_OFFSET &&
                   0 == strcmp(running->label, "boot_a")) ||
                  (running->address == SPECTER_BOOT_B_OFFSET &&
                   0 == strcmp(running->label, "boot_b")));
  if (!valid_boot_role) {
    blsys_init();
    blsys_fatal_error(
        "Specter Bootloader is not running from boot_a or boot_b");
  }

  bl_args_t args;
  memset(&args, 0, sizeof(args));
  args.loaded_from = running->address;
  args.startup_version = 1U;
  args.struct_crc = crc32_fast(&args, offsetof(bl_args_t, struct_crc), 0U);

  uint32_t flags = 0U;
  if (bl_version_is_rc(bl_decode_version_tag(version_tag))) {
    flags |= bl_flag_allow_rc_versions;
  }
  bl_status_t status = bootloader_run(&args, flags);
  if (bootloader_has_error(status)) {
    blsys_init();
    blsys_fatal_error(bootloader_status_text(status));
  }

  bl_addr_t main_address = 0U;
  if (!blsys_flash_map_get_items(1, bl_flash_firmware_base, &main_address) ||
      !blsys_start_firmware(main_address, 1U)) {
    blsys_init();
    blsys_fatal_error("No approved Main Firmware found");
  }
}
