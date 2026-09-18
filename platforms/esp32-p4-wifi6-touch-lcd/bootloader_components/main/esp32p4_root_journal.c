/**
 * @file esp32p4_root_journal.c
 * @brief Root Loader plaintext trial journal I/O.
 */

#include "esp32p4_root_journal.h"

#include "bootloader_flash_priv.h"
#include "crc32.h"
#include "esp32p4_test_hooks.h"

specter_boot_journal_state_t specter_root_journal_state(
    specter_esp32p4_role_t role, uint32_t sequence) {
  specter_boot_journal_state_t state = specter_journal_none;
  if (!sequence ||
      (role != specter_role_boot_a && role != specter_role_boot_b)) {
    return state;
  }
  uint32_t offset =
      SPECTER_JOURNAL_PARTITION_OFFSET + specter_journal_sector_offset(role);
  const uint32_t end = offset + SPECTER_JOURNAL_SECTOR_SIZE;
  specter_boot_journal_record_t record;
  while (offset + sizeof(record) <= end) {
    if (bootloader_flash_read(offset, &record, sizeof(record), false) !=
            ESP_OK ||
        specter_journal_slot_erased(&record)) {
      break;
    }
    if (record.magic == SPECTER_JOURNAL_MAGIC &&
        record.revision == SPECTER_JOURNAL_REVISION &&
        record.sequence == sequence &&
        (record.state == specter_journal_attempted ||
         record.state == specter_journal_confirmed) &&
        record.commit_crc ==
            crc32_fast(&record,
                       offsetof(specter_boot_journal_record_t, commit_crc),
                       0U)) {
      state = (specter_boot_journal_state_t)record.state;
    }
    offset += sizeof(record);
  }
  return state;
}

bool specter_root_journal_append(specter_esp32p4_role_t role, uint32_t sequence,
                                 specter_boot_journal_state_t state) {
  if (!sequence ||
      (role != specter_role_boot_a && role != specter_role_boot_b) ||
      (state != specter_journal_attempted &&
       state != specter_journal_confirmed)) {
    return false;
  }
  uint32_t offset =
      SPECTER_JOURNAL_PARTITION_OFFSET + specter_journal_sector_offset(role);
  const uint32_t end = offset + SPECTER_JOURNAL_SECTOR_SIZE;
  specter_boot_journal_record_t existing;
  while (offset + sizeof(existing) <= end) {
    if (bootloader_flash_read(offset, &existing, sizeof(existing), false) !=
        ESP_OK) {
      return false;
    }
    if (specter_journal_slot_erased(&existing)) {
      break;
    }
    offset += sizeof(existing);
  }
  if (offset + sizeof(existing) > end) {
    return false;
  }

  specter_boot_journal_record_t record = {
      .magic = SPECTER_JOURNAL_MAGIC,
      .revision = SPECTER_JOURNAL_REVISION,
      .sequence = sequence,
      .state = state,
      .commit_crc = UINT32_MAX,
  };
  const size_t prefix = offsetof(specter_boot_journal_record_t, commit_crc);
  record.commit_crc = crc32_fast(&record, prefix, 0U);
#if CONFIG_SPECTER_E2E_TEST_HOOKS
  uint32_t hook = state == specter_journal_attempted
                      ? specter_test_attempt_before
                      : specter_test_confirm_before;
  specter_test_boundary(hook, role, sequence);
#endif
  if (bootloader_flash_write(offset, &record, prefix, false) != ESP_OK) {
    return false;
  }
#if CONFIG_SPECTER_E2E_TEST_HOOKS
  specter_test_boundary(hook + 1U, role, sequence);
#endif
  if (bootloader_flash_write(offset + prefix, (uint8_t*)&record + prefix,
                             sizeof(record) - prefix, false) != ESP_OK) {
    return false;
  }
#if CONFIG_SPECTER_E2E_TEST_HOOKS
  specter_test_boundary(hook + 2U, role, sequence);
#endif
  return true;
}
