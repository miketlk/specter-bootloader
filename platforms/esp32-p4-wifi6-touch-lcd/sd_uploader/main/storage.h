/** @file storage.h
 * @brief Private writable SD adapter, never linked into Specter.
 */
#pragma once
#include <stddef.h>
#include <stdint.h>
enum {
  SDU_OK,
  SDU_BAD,
  SDU_STALE,
  SDU_BUSY,
  SDU_PATH,
  SDU_RANGE,
  SDU_OFFSET,
  SDU_NO_MEDIA,
  SDU_MOUNT,
  SDU_SPACE,
  SDU_IO,
  SDU_DIGEST,
  SDU_EXISTS,
  SDU_UNCERTAIN
};
typedef struct {
  char name[129];
  uint64_t size;
  int directory, temporary;
} sdu_entry;
extern const char* sdu_state;
extern uint64_t sdu_offset, sdu_length, sdu_capacity;
extern uint8_t sdu_digest[32];
extern uint8_t sdu_observed_digest[32];
extern uint64_t sdu_observed_length;
extern uint64_t sdu_commit_times[4];
extern uint64_t sdu_read_us, sdu_hash_us, sdu_write_us;
extern char sdu_final[129], sdu_cid[33];
int sdu_mount(void);
int sdu_begin(const char* name, uint64_t length, const uint8_t digest[32],
              const uint8_t session[16]);
int sdu_write(uint64_t offset, const uint8_t* data, size_t length);
int sdu_commit(void (*progress)(uint64_t));
int sdu_abort(void);
int sdu_remove(const char* name);
int sdu_list(uint64_t cursor, sdu_entry entries[8], size_t* count,
             uint64_t* next);
int sdu_release(void);

extern uint32_t sdu_media_error;
extern const char* sdu_media_stage;

int sdu_verify_existing(const char* name, uint64_t length,
                        const uint8_t digest[32], void (*progress)(uint64_t));

int sdu_cleanup(const char* name);
