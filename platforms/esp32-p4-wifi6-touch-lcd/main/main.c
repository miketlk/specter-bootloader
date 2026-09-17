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
#include "esp32p4_reset.h"
#include "esp32p4_trailer.h"
#include "esp_ota_ops.h"
#include "esp_system.h"

#define SPECTER_ROOT_LOADER_VERSION 100000099U

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

  specter_esp32p4_role_t running_role =
      running->address == SPECTER_BOOT_A_OFFSET ? specter_role_boot_a
                                                : specter_role_boot_b;
  if (!blsys_init()) {
    blsys_fatal_error("Specter Bootloader initialization failed");
  }
  specter_approval_record_t running_approval;
  if (!specter_esp32p4_approval_read(running_role, &running_approval, true)) {
    blsys_fatal_error("Running Specter Bootloader is not approved");
  }
  specter_boot_journal_state_t running_state =
      specter_esp32p4_journal_state(running_role, running_approval.sequence);
  bool running_trial = running_state == specter_journal_attempted;
  if (!running_trial && running_state != specter_journal_confirmed) {
    blsys_fatal_error("Specter Bootloader trial state is invalid");
  }
  blsys_deinit();

  bl_args_t args;
  memset(&args, 0, sizeof(args));
  args.loaded_from = running->address;
  args.startup_version = SPECTER_ROOT_LOADER_VERSION;
  args.struct_crc = crc32_fast(&args, offsetof(bl_args_t, struct_crc), 0U);

  uint32_t flags = 0U;
  if (running_trial) {
    // Root Loader must durably confirm this copy before it can update the
    // inactive slot, which still holds the only confirmed fallback.
    flags |= bl_flag_check_only;
  }
  if (bl_version_is_rc(bl_decode_version_tag(version_tag))) {
    flags |= bl_flag_allow_rc_versions;
  }
  bl_status_t status = bootloader_run(&args, flags);
  if (bootloader_has_error(status)) {
    blsys_init();
    blsys_fatal_error(bootloader_status_text(status));
  }
  if (running_trial) {
    if (!blsys_init()) {
      blsys_fatal_error("Specter Bootloader initialization failed");
    }
    if (!specter_esp32p4_reset_request_write(specter_rtc_confirm_bootloader,
                                             running_role,
                                             running_approval.sequence, 0U)) {
      blsys_fatal_error("Unable to request Bootloader confirmation");
    }
    esp_restart();
  }

  // Successful upgrades wait at the completion alert for a physical reset.
  // On normal return, honor Root's selection and proceed to Main: a newer
  // unattempted inactive image may have been rejected before this fallback ran.
  bl_addr_t main_address = 0U;
  if (!blsys_flash_map_get_items(1, bl_flash_firmware_base, &main_address) ||
      !blsys_start_firmware(main_address, 1U)) {
    blsys_init();
    blsys_fatal_error("No approved Main Firmware found");
  }
}
