/**
 * @file esp32p4_root_journal.h
 * @brief Root Loader journal access for the two fixed Bootloader roles.
 */

#ifndef ESP32P4_ROOT_JOURNAL_H_INCLUDED
#define ESP32P4_ROOT_JOURNAL_H_INCLUDED

#include "esp32p4_boot_contract.h"

/// Reads the latest committed state for a Bootloader approval sequence.
specter_boot_journal_state_t specter_root_journal_state(
    specter_esp32p4_role_t role, uint32_t sequence);

/// Appends a plaintext marker without overwriting torn records or another role.
bool specter_root_journal_append(specter_esp32p4_role_t role, uint32_t sequence,
                                 specter_boot_journal_state_t state);

#endif  // ESP32P4_ROOT_JOURNAL_H_INCLUDED
