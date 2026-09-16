/**
 * @file esp32p4_trailer.h
 * @brief ESP32-P4 approval trailer and append-only boot journal format.
 */

#ifndef ESP32P4_TRAILER_H_INCLUDED
#define ESP32P4_TRAILER_H_INCLUDED

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp32p4_platform.h"

/// Returns the exact, unencrypted shared journal partition, or NULL.
const esp_partition_t* specter_esp32p4_journal_partition(void);

/// Invalidates a role's approval before the first candidate image mutation.
bool specter_esp32p4_approval_invalidate(specter_esp32p4_role_t role);

/// Commits only when the copied ESP image matches the signed exact digest.
bool specter_esp32p4_approval_create_authorized(
    specter_esp32p4_role_t role, uint32_t image_length,
    uint32_t semantic_version, const uint8_t expected_sha256[32]);

/// Reads and validates a committed approval record, optionally hashing image.
bool specter_esp32p4_approval_read(specter_esp32p4_role_t role,
                                   specter_approval_record_t* record,
                                   bool verify_image);

/// Returns the latest valid state for an approval sequence.
specter_boot_journal_state_t specter_esp32p4_journal_state(
    specter_esp32p4_role_t role, uint32_t sequence);

#endif  // ESP32P4_TRAILER_H_INCLUDED
