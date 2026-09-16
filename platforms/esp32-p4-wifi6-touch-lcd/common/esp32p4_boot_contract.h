/**
 * @file esp32p4_boot_contract.h
 * @brief App/Root Loader wire ABI for fixed roles, approvals, and requests.
 */

#ifndef ESP32P4_BOOT_CONTRACT_H_INCLUDED
#define ESP32P4_BOOT_CONTRACT_H_INCLUDED

#include <stdint.h>

#define SPECTER_ESP32P4_TRAILER_SIZE 0x1000U
#define SPECTER_JOURNAL_OFFSET 0x80U
#define SPECTER_APPROVAL_MAGIC "SPAPRV2"
#define SPECTER_APPROVAL_REVISION 2U
#define SPECTER_APPROVAL_STATUS_APPROVED 0x41505052U
#define SPECTER_JOURNAL_MAGIC 0x4A525053U
#define SPECTER_JOURNAL_REVISION 2U
#define SPECTER_FLASH_WRITE_GRANULE 16U
#define SPECTER_COMMIT_PADDING_SIZE \
  (SPECTER_FLASH_WRITE_GRANULE - sizeof(uint32_t))
#define SPECTER_JOURNAL_WRITE_GRANULE 32U
#define SPECTER_JOURNAL_COMMIT_PADDING_SIZE \
  (SPECTER_JOURNAL_WRITE_GRANULE - sizeof(uint32_t))
#define SPECTER_RTC_REQUEST_MAGIC 0x51525053U
#define SPECTER_RTC_REQUEST_REVISION 1U

typedef enum specter_esp32p4_role {
  specter_role_invalid = 0,
  specter_role_boot_a = 1,
  specter_role_boot_b = 2,
  specter_role_main = 3,
} specter_esp32p4_role_t;

typedef enum specter_boot_journal_state {
  specter_journal_none = 0,
  specter_journal_attempted = 0x4154544DU,
  specter_journal_confirmed = 0x434F4E46U,
} specter_boot_journal_state_t;

typedef enum specter_rtc_command {
  specter_rtc_boot_main = 1,
  specter_rtc_confirm_bootloader = 2,
} specter_rtc_command_t;

typedef struct __attribute__((packed, aligned(4))) specter_approval_record {
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
  uint32_t prefix_padding;
  uint32_t commit_crc;
  uint8_t commit_padding[SPECTER_COMMIT_PADDING_SIZE];
} specter_approval_record_t;

typedef struct __attribute__((packed, aligned(4))) specter_boot_journal_record {
  uint32_t magic;
  uint32_t revision;
  uint32_t sequence;
  uint32_t state;
  uint8_t prefix_padding[SPECTER_JOURNAL_WRITE_GRANULE - 4U * sizeof(uint32_t)];
  uint32_t commit_crc;
  uint8_t commit_padding[SPECTER_JOURNAL_COMMIT_PADDING_SIZE];
} specter_boot_journal_record_t;

typedef struct __attribute__((packed, aligned(4))) specter_rtc_request {
  uint32_t magic;
  uint32_t revision;
  uint32_t command;
  uint32_t target;
  uint32_t sequence;
  uint32_t argument;
  uint32_t crc;
} specter_rtc_request_t;

#endif  // ESP32P4_BOOT_CONTRACT_H_INCLUDED
