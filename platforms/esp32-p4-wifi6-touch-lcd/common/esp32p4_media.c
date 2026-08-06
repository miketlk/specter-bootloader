/**
 * @file esp32p4_media.c
 * @brief Shared Waveshare SDMMC and read-only POSIX VFS adapter.
 */

#include <errno.h>
#include <string.h>

#include "bl_syscalls.h"
#include "driver/sdmmc_host.h"
#include "esp32p4_platform.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"

static const char* TAG = "specter-media";
static sdmmc_card_t* mounted_card;

static bool wildcard_match(const char* pattern, const char* text) {
  if (!pattern || !text) {
    return false;
  }
  if ('\0' == *pattern) {
    return '\0' == *text;
  }
  if ('*' == *pattern) {
    do {
      if (wildcard_match(pattern + 1, text)) {
        return true;
      }
    } while ('\0' != *text++);
    return false;
  }
  return *pattern == *text && wildcard_match(pattern + 1, text + 1);
}

uint32_t blsys_media_devices(void) { return 1U; }

const char* blsys_media_name(uint32_t device_idx) {
  return 0U == device_idx ? "microSD" : "unknown";
}

bool blsys_media_check(uint32_t device_idx) {
  // Both boards expose no confirmed card-detect GPIO. The non-formatting mount
  // in blsys_media_mount() is therefore the authoritative presence probe.
  return 0U == device_idx && (mounted_card || blsys_media_mount(device_idx));
}

bool blsys_media_mount(uint32_t device_idx) {
  if (0U != device_idx) {
    return false;
  }
  if (mounted_card) {
    return true;
  }

  esp_vfs_fat_sdmmc_mount_config_t mount_config = {
      .format_if_mount_failed = false,
      .max_files = 4,
      .allocation_unit_size = 16U * 1024U,
  };
  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
  slot.width = 4;
  slot.clk = GPIO_NUM_43;
  slot.cmd = GPIO_NUM_44;
  slot.d0 = GPIO_NUM_39;
  slot.d1 = GPIO_NUM_40;
  slot.d2 = GPIO_NUM_41;
  slot.d3 = GPIO_NUM_42;
  slot.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;

  esp_err_t result =
      esp_vfs_fat_sdmmc_mount(SPECTER_ESP32P4_MEDIA_MOUNT_POINT, &host, &slot,
                              &mount_config, &mounted_card);
  if (ESP_OK != result) {
    mounted_card = NULL;
    ESP_LOGW(TAG, "microSD mount probe failed: %s", esp_err_to_name(result));
    return false;
  }
  return true;
}

void blsys_media_umount(void) {
  if (mounted_card) {
    esp_vfs_fat_sdcard_unmount(SPECTER_ESP32P4_MEDIA_MOUNT_POINT, mounted_card);
    mounted_card = NULL;
  }
}

static const char* find_next(bl_ffind_ctx_t* ctx) {
  if (!ctx || !ctx->dir) {
    return NULL;
  }
  struct dirent* entry;
  while ((entry = readdir(ctx->dir))) {
    if (wildcard_match(ctx->pattern, entry->d_name)) {
      int length = snprintf(ctx->result, sizeof(ctx->result), "%s/%s",
                            ctx->directory, entry->d_name);
      if (length > 0 && (size_t)length < sizeof(ctx->result)) {
        return ctx->result;
      }
      return NULL;
    }
  }
  return NULL;
}

const char* blsys_ffind_first(bl_ffind_ctx_t* ctx, const char* path,
                              const char* pattern) {
  if (!ctx || !path || !pattern || strlen(pattern) >= sizeof(ctx->pattern)) {
    return NULL;
  }
  memset(ctx, 0, sizeof(*ctx));
  const char* directory = ('\0' == *path || 0 == strcmp(path, "/"))
                              ? SPECTER_ESP32P4_MEDIA_MOUNT_POINT
                              : path;
  if (strlen(directory) >= sizeof(ctx->directory)) {
    return NULL;
  }
  strcpy(ctx->directory, directory);
  strcpy(ctx->pattern, pattern);
  ctx->dir = opendir(ctx->directory);
  return find_next(ctx);
}

const char* blsys_ffind_next(bl_ffind_ctx_t* ctx) { return find_next(ctx); }

void blsys_ffind_close(bl_ffind_ctx_t* ctx) {
  if (ctx && ctx->dir) {
    closedir(ctx->dir);
    ctx->dir = NULL;
  }
}

bl_file_t blsys_fopen(bl_file_obj_t* object, const char* filename,
                      const char* mode) {
  (void)object;
  if (!filename || !mode || 0 != strcmp(mode, "rb")) {
    return NULL;
  }
  return fopen(filename, mode);
}

size_t blsys_fread(void* ptr, size_t size, size_t count, bl_file_t file) {
  return file ? fread(ptr, size, count, file) : 0U;
}

bl_foffset_t blsys_ftell(bl_file_t file) {
  return file ? (bl_foffset_t)ftell(file) : -1L;
}

int blsys_fseek(bl_file_t file, bl_foffset_t offset, int origin) {
  return file ? fseek(file, offset, origin) : -1;
}

bl_fsize_t blsys_fsize(bl_file_t file) {
  if (!file) {
    return 0U;
  }
  long current = ftell(file);
  if (current < 0 || 0 != fseek(file, 0L, SEEK_END)) {
    return 0U;
  }
  long end = ftell(file);
  if (0 != fseek(file, current, SEEK_SET) || end < 0) {
    return 0U;
  }
  return (bl_fsize_t)end;
}

int blsys_feof(bl_file_t file) { return file ? feof(file) : 1; }

int blsys_fclose(bl_file_t file) { return file ? fclose(file) : EOF; }
