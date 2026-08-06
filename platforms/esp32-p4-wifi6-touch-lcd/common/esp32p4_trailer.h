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

#define SPECTER_APPROVAL_MAGIC "SPAPRV2"
#define SPECTER_APPROVAL_REVISION 2U
#define SPECTER_APPROVAL_STATUS_APPROVED 0x41505052U
#define SPECTER_JOURNAL_MAGIC 0x4A525053U
#define SPECTER_JOURNAL_REVISION 2U
#define SPECTER_FLASH_WRITE_GRANULE 16U
#define SPECTER_COMMIT_PADDING_SIZE \
  (SPECTER_FLASH_WRITE_GRANULE - sizeof(uint32_t))

typedef enum specter_boot_journal_state {
  specter_journal_attempted = 0x4154544DU,
  specter_journal_confirmed = 0x434F4E46U,
} specter_boot_journal_state_t;

typedef struct __attribute__((packed)) specter_approval_record {
  char magic[8];
  uint32_t revision;
  uint32_t record_size;
  char platform[40];
  uint32_t role;
  uint32_t semantic_version;
  uint32_t image_length;
  uint32_t sequence;
  uint32_t status;
  uint8_t image_sha256[32];
  /// Extends the CRC-covered prefix to a flash-encryption write granule.
  uint32_t prefix_padding;
  /// Starts the final, separately written commit granule.
  uint32_t commit_crc;
  uint8_t commit_padding[SPECTER_COMMIT_PADDING_SIZE];
} specter_approval_record_t;

typedef struct __attribute__((packed)) specter_boot_journal_record {
  uint32_t magic;
  uint32_t revision;
  uint32_t sequence;
  uint32_t state;
  /// Starts the final, separately written commit granule.
  uint32_t commit_crc;
  uint8_t commit_padding[SPECTER_COMMIT_PADDING_SIZE];
} specter_boot_journal_record_t;

/// Invalidates a role's approval before the first candidate image mutation.
bool specter_esp32p4_approval_invalidate(specter_esp32p4_role_t role);

/// Commits an approval record after validating the exact installed ESP image.
bool specter_esp32p4_approval_create(specter_esp32p4_role_t role,
                                     uint32_t image_length,
                                     uint32_t semantic_version);

/// Reads and validates a committed approval record, optionally hashing image.
bool specter_esp32p4_approval_read(specter_esp32p4_role_t role,
                                   specter_approval_record_t* record,
                                   bool verify_image);

/// Appends a power-loss-safe attempted or confirmed boot journal marker.
bool specter_esp32p4_journal_append(specter_esp32p4_role_t role,
                                    uint32_t sequence,
                                    specter_boot_journal_state_t state);

#endif  // ESP32P4_TRAILER_H_INCLUDED
