/** @file storage.c
 * @brief Non-formatting SDMMC/VFS transactions with independent readback.
 */
#include "storage.h"

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

#include "protocol.h"
#ifdef SDU_HOST_TEST
#ifdef ESP_PLATFORM
#error "Host fault injection cannot be enabled on the device"
#endif
#include "storage_host.h"
#else
#include "board.h"
#include "diskio_impl.h"
#include "diskio_sdmmc.h"
#include "driver/sdmmc_host.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "ff.h"
#include "mbedtls/sha256.h"
#include "protocol.h"
#include "sd_pwr_ctrl_by_on_chip_ldo.h"
#include "sdmmc_cmd.h"
#endif
#ifndef SDU_ROOT
#define SDU_ROOT "/sd"
#endif

const char* sdu_state = "NO_MEDIA";
uint32_t sdu_media_error;
const char* sdu_media_stage = "not-probed";
uint64_t sdu_offset, sdu_length, sdu_capacity;
uint8_t sdu_digest[32];
uint8_t sdu_observed_digest[32];
uint64_t sdu_observed_length;
uint64_t sdu_commit_times[4];
uint64_t sdu_read_us, sdu_hash_us, sdu_write_us;
char sdu_final[129], sdu_cid[33];
static sdmmc_card_t card;
static FATFS* fs;
static FILE* active;
static char temporary[180], destination[180];
static uint8_t read_buffer[SDU_CHUNK] SDU_BUFFER __attribute__((aligned(64)));
// One file is open at a time. An explicit stdio buffer prevents the small
// libc default from fragmenting large reads into single-sector VFS requests.
static uint8_t file_buffer[SDU_CHUNK] SDU_BUFFER __attribute__((aligned(64)));
static bool mounted, host_up, vfs_up;
static sd_pwr_ctrl_handle_t power;

static uint64_t storage_time(void) {
#ifdef SDU_HOST_TEST
  return 0;
#else
  return esp_timer_get_time();
#endif
}

static int io_error(void) {
  sdu_state = "UNCERTAIN";
  return errno == ENOSPC ? SDU_SPACE : SDU_IO;
}
static int unavailable(void) {
  if (!strcmp(sdu_state, "UNCERTAIN")) return SDU_UNCERTAIN;
  if (!strcmp(sdu_state, "NO_MEDIA")) return SDU_NO_MEDIA;
  return SDU_BUSY;
}
static bool idle(void) {
  return !strcmp(sdu_state, "IDLE") || !strcmp(sdu_state, "COMMITTED");
}

int sdu_mount(void) {
  if (mounted) return SDU_OK;
  if (strcmp(sdu_state, "NO_MEDIA")) return SDU_BUSY;
  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  // The pinned driver otherwise bounces unaligned VFS buffers one sector at
  // a time. Bound its internal DMA scratch space to one protocol chunk.
  host.unaligned_multi_block_rw_max_chunk_size = SDU_CHUNK / 512;
  sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
  slot.width = 4;
  slot.clk = SPECTER_SDMMC_CLK_GPIO;
  slot.cmd = SPECTER_SDMMC_CMD_GPIO;
  slot.d0 = SPECTER_SDMMC_D0_GPIO;
  slot.d1 = SPECTER_SDMMC_D1_GPIO;
  slot.d2 = SPECTER_SDMMC_D2_GPIO;
  slot.d3 = SPECTER_SDMMC_D3_GPIO;
  slot.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;
  // Vendor SD example uses LDO4 for SD IO power on both boards.
  sd_pwr_ctrl_ldo_config_t power_config = {.ldo_chan_id = 4};
  if (sd_pwr_ctrl_new_on_chip_ldo(&power_config, &power) != ESP_OK)
    return SDU_MOUNT;
  host.pwr_ctrl_handle = power;
  if (sdmmc_host_init() != ESP_OK) {
    sd_pwr_ctrl_del_on_chip_ldo(power);
    power = NULL;
    return SDU_MOUNT;
  }
  host_up = true;
  sdu_media_stage = "slot-init";
  sdu_media_error = sdmmc_host_init_slot(host.slot, &slot);
  if (sdu_media_error) goto failed;
  sdu_media_stage = "card-init";
  sdu_media_error = sdmmc_card_init(&host, &card);
  if (sdu_media_error) goto failed;
  ff_diskio_register_sdmmc(0, &card);
  sdu_media_stage = "vfs-register";
  sdu_media_error = esp_vfs_fat_register(SDU_ROOT, "0:", 4, &fs);
  if (sdu_media_error) goto failed;
  vfs_up = true;
  sdu_media_stage = "fat-mount";
  sdu_media_error = f_mount(fs, "0:", 1);
  if (sdu_media_error) goto failed;
  sdu_capacity = (uint64_t)card.csd.capacity * card.csd.sector_size;
  // Pinned IDF retains raw_cid only for MMC. Normalize decoded SD CID fields
  // (manufacturer/OEM/name/revision/serial/date) instead of reporting zeros.
  uint8_t cid[16] = {card.cid.mfg_id, card.cid.oem_id >> 8, card.cid.oem_id};
  memcpy(cid + 3, card.cid.name, 5);
  cid[8] = card.cid.revision;
  uint32_t serial = (uint32_t)card.cid.serial;
  for (int i = 0; i < 4; ++i) cid[9 + i] = serial >> (24 - 8 * i);
  cid[13] = card.cid.date >> 8;
  cid[14] = card.cid.date;
  for (size_t i = 0; i < sizeof(cid); ++i)
    snprintf(sdu_cid + 2 * i, 3, "%02x", cid[i]);
  mounted = true;
  sdu_state = "IDLE";
  return SDU_OK;
failed:
  bool cleanup_failed = f_mount(NULL, "0:", 0) != FR_OK;
  if (vfs_up) {
    if (esp_vfs_fat_unregister_path(SDU_ROOT) != ESP_OK) cleanup_failed = true;
    vfs_up = false;
  }
  ff_diskio_unregister(0);
  if (sdmmc_host_deinit() != ESP_OK) {
    sdu_state = "UNCERTAIN";
    return SDU_UNCERTAIN;
  }
  host_up = false;
  if (sd_pwr_ctrl_del_on_chip_ldo(power) != ESP_OK) {
    sdu_state = "UNCERTAIN";
    return SDU_UNCERTAIN;
  }
  power = NULL;
  if (cleanup_failed) {
    sdu_state = "UNCERTAIN";
    return SDU_UNCERTAIN;
  }
  return SDU_MOUNT;
}

int sdu_begin(const char* name, uint64_t length, const uint8_t digest[32],
              const uint8_t session[16]) {
  if (!idle()) return unavailable();
  if (!sdu_name(name)) return SDU_PATH;
  if (length > INT32_MAX) return SDU_RANGE;
  snprintf(destination, sizeof(destination), SDU_ROOT "/%s", name);
  struct stat st;
  if (!stat(destination, &st)) return SDU_EXISTS;
  if (errno != ENOENT) return io_error();
  DWORD clusters;
  FATFS* volume;
  if (f_getfree("0:", &clusters, &volume) != FR_OK) return io_error();
  uint64_t cluster = (uint64_t)volume->csize * card.csd.sector_size;
  if (length + 2 * cluster > (uint64_t)clusters * cluster) return SDU_SPACE;
  char id[33];
  for (int i = 0; i < 16; ++i) snprintf(id + 2 * i, 3, "%02x", session[i]);
  snprintf(temporary, sizeof(temporary), SDU_ROOT "/_sdu_%s.part", id);
  // Exclusive creation also resolves case-insensitive and short-name
  // collisions.
  active = fopen(temporary, "wbx");
  if (!active) return errno == EEXIST ? SDU_EXISTS : io_error();
  if (setvbuf(active, (char*)file_buffer, _IOFBF, sizeof(file_buffer))) {
    fclose(active);
    active = NULL;
    return io_error();
  }
  strcpy(sdu_final, name);
  memcpy(sdu_digest, digest, 32);
  sdu_length = length;
  sdu_offset = 0;
  sdu_state = "RECEIVING";
  return SDU_OK;
}

int sdu_write(uint64_t offset, const uint8_t* data, size_t length) {
  if (strcmp(sdu_state, "RECEIVING") || !active) return SDU_BUSY;
  if (offset != sdu_offset) return SDU_OFFSET;
  if (!length || length > SDU_CHUNK || length > sdu_length - sdu_offset)
    return SDU_RANGE;
  uint64_t started = storage_time();
  size_t written = fwrite(data, 1, length, active);
  sdu_write_us = storage_time() - started;
  if (written != length) return io_error();
  sdu_offset += length;
  return SDU_OK;
}

static int verify(const char* path, void (*progress)(uint64_t)) {
  FILE* file = fopen(path, "rb");
  if (!file) return io_error();
  if (setvbuf(file, (char*)file_buffer, _IOFBF, sizeof(file_buffer))) {
    fclose(file);
    return io_error();
  }
  mbedtls_sha256_context hash;
  mbedtls_sha256_init(&hash);
  int failed = mbedtls_sha256_starts(&hash, 0);
  uint64_t count = 0, reported = 0;
  while (!failed) {
    uint64_t started = storage_time();
    size_t n = fread(read_buffer, 1, sizeof(read_buffer), file);
    sdu_read_us += storage_time() - started;
    if (n) {
      count += n;
      started = storage_time();
      if (count > sdu_length || mbedtls_sha256_update(&hash, read_buffer, n))
        failed = 1;
      sdu_hash_us += storage_time() - started;
      if (progress && count - reported >= 262144) {
        progress(count);
        reported = count;
      }
    }
    if (n < sizeof(read_buffer)) {
      if (ferror(file)) failed = 1;
      break;
    }
  }
  uint8_t digest[32];
  if (mbedtls_sha256_finish(&hash, digest)) failed = 1;
  mbedtls_sha256_free(&hash);
  if (fclose(file)) failed = 1;
  if (failed) return io_error();
  sdu_observed_length = count;
  memcpy(sdu_observed_digest, digest, 32);
  if (count != sdu_length || memcmp(digest, sdu_digest, 32)) {
    sdu_state = "UNCERTAIN";
    return SDU_DIGEST;
  }
  return SDU_OK;
}

int sdu_verify_existing(const char* name, uint64_t length,
                        const uint8_t digest[32], void (*progress)(uint64_t)) {
  if (!idle()) return unavailable();
  if (!sdu_name(name)) return SDU_PATH;
  if (length > INT32_MAX) return SDU_RANGE;
  char path[180];
  snprintf(path, sizeof(path), SDU_ROOT "/%s", name);
  const char* previous = sdu_state;
  uint64_t old_length = sdu_length;
  uint8_t old_digest[32];
  memcpy(old_digest, sdu_digest, 32);
  sdu_length = length;
  memcpy(sdu_digest, digest, 32);
  sdu_state = "VERIFYING";
  int status = verify(path, progress);
  sdu_length = old_length;
  memcpy(sdu_digest, old_digest, 32);
  if (!status) sdu_state = previous;
  return status;
}

int sdu_commit(void (*progress)(uint64_t)) {
  if (!strcmp(sdu_state, "COMMITTED")) return SDU_OK;
  if (strcmp(sdu_state, "RECEIVING") || !active) return SDU_BUSY;
  if (sdu_offset != sdu_length) return SDU_OFFSET;
  sdu_state = "VERIFYING";
  memset(sdu_commit_times, 0, sizeof(sdu_commit_times));
  sdu_read_us = sdu_hash_us = 0;
  uint64_t started = storage_time();
  int failed = fflush(active);
  if (fsync(fileno(active))) failed = 1;
  if (fclose(active)) failed = 1;
  active = NULL;
  sdu_commit_times[0] = storage_time() - started;
  if (failed) return io_error();
  started = storage_time();
  int status = verify(temporary, progress);
  sdu_commit_times[1] = storage_time() - started;
  if (status) return status;
  struct stat st;
  if (!stat(destination, &st)) {
    sdu_state = "UNCERTAIN";
    return SDU_EXISTS;
  }
  started = storage_time();
  if (errno != ENOENT || rename(temporary, destination)) return io_error();
  sdu_commit_times[2] = storage_time() - started;
  started = storage_time();
  status = verify(destination, progress);
  sdu_commit_times[3] = storage_time() - started;
  if (!status) {
    temporary[0] = 0;
    sdu_state = "COMMITTED";
  }
  return status;
}

int sdu_abort(void) {
  if (!strcmp(sdu_state, "UNCERTAIN")) return SDU_UNCERTAIN;
  int failed = 0;
  if (active) {
    failed = fclose(active);
    active = NULL;
  }
  if (temporary[0] && unlink(temporary) && errno != ENOENT) failed = 1;
  if (failed) return io_error();
  temporary[0] = 0;
  if (mounted) sdu_state = "IDLE";
  return SDU_OK;
}

int sdu_remove(const char* name) {
  if (!idle()) return unavailable();
  if (!sdu_name(name)) return SDU_PATH;
  char path[180];
  snprintf(path, sizeof(path), SDU_ROOT "/%s", name);
  struct stat st;
  if (stat(path, &st)) return errno == ENOENT ? SDU_OK : io_error();
  if (!S_ISREG(st.st_mode)) return SDU_PATH;
  return unlink(path) ? io_error() : SDU_OK;
}

int sdu_cleanup(const char* name) {
  // Recovery-only deletion of our exact generated namespace, after host CID
  // verification. Ordinary file operations continue to reject this namespace.
  if (!idle()) return unavailable();
  if (strlen(name) != 42 || strncmp(name, "_sdu_", 5) ||
      strspn(name + 5, "0123456789abcdef") != 32 || strcmp(name + 37, ".part"))
    return SDU_PATH;
  char path[180];
  snprintf(path, sizeof(path), SDU_ROOT "/%s", name);
  struct stat st;
  if (stat(path, &st)) return errno == ENOENT ? SDU_OK : io_error();
  if (!S_ISREG(st.st_mode)) return SDU_PATH;
  return unlink(path) ? io_error() : SDU_OK;
}

int sdu_list(uint64_t cursor, sdu_entry entries[8], size_t* count,
             uint64_t* next) {
  *count = 0;
  *next = 0;
  if (!idle()) return unavailable();
  if (cursor > 65536) return SDU_RANGE;
  DIR* directory = opendir(SDU_ROOT);
  if (!directory) return io_error();
  uint64_t index = 0;
  struct dirent* ent;
  int status = SDU_OK;
  errno = 0;
  while ((ent = readdir(directory))) {
    if (index++ < cursor) continue;
    if (*count == 8) {
      *next = index - 1;
      break;
    }
    if (strlen(ent->d_name) > 128) {
      status = SDU_RANGE;
      break;
    }
    sdu_entry* entry = &entries[(*count)++];
    strcpy(entry->name, ent->d_name);
    char path[180];
    snprintf(path, sizeof(path), SDU_ROOT "/%s", entry->name);
    struct stat st;
    if (stat(path, &st)) {
      status = io_error();
      break;
    }
    entry->size = st.st_size;
    entry->directory = S_ISDIR(st.st_mode);
    entry->temporary = !strncasecmp(entry->name, "_sdu_", 5);
    errno = 0;
  }
  if (!ent && errno) status = io_error();
  if (closedir(directory)) status = io_error();
  return status;
}

int sdu_release(void) {
  if (!strcmp(sdu_state, "RELEASED")) return SDU_OK;
  if (!idle()) return unavailable();
  if (f_mount(NULL, "0:", 0) != FR_OK) return io_error();
  if (esp_vfs_fat_unregister_path(SDU_ROOT) != ESP_OK) return io_error();
  vfs_up = false;
  ff_diskio_unregister(0);
  if (host_up && sdmmc_host_deinit() != ESP_OK) return io_error();
  host_up = false;
  if (power && sd_pwr_ctrl_del_on_chip_ldo(power) != ESP_OK) return io_error();
  power = NULL;
  mounted = false;
  sdu_state = "RELEASED";
  return SDU_OK;
}
