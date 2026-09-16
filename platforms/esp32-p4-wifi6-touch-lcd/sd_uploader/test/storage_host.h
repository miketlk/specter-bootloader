/** @file storage_host.h
 * @brief POSIX host shim for fault-testing the production storage transaction.
 */
#pragma once
#include <openssl/sha.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#define ESP_OK 0
#define FR_OK 0
#define SDMMC_SLOT_FLAG_INTERNAL_PULLUP 1
#define SDMMC_HOST_DEFAULT() {0}
#define SDMMC_SLOT_CONFIG_DEFAULT() {0}
typedef void* sd_pwr_ctrl_handle_t;
typedef struct {
  int ldo_chan_id;
} sd_pwr_ctrl_ldo_config_t;
typedef struct {
  int slot;
  sd_pwr_ctrl_handle_t pwr_ctrl_handle;
  size_t unaligned_multi_block_rw_max_chunk_size;
} sdmmc_host_t;
typedef struct {
  int width, clk, cmd, d0, d1, d2, d3, flags;
} sdmmc_slot_config_t;
typedef struct {
  int mfg_id, oem_id;
  char name[8];
  int revision, serial, date;
} host_cid;
typedef struct {
  struct {
    uint32_t capacity, sector_size;
  } csd;
  host_cid cid;
} sdmmc_card_t;
typedef struct {
  unsigned csize;
} FATFS;
typedef uint32_t DWORD;
extern int host_fault;
static inline int sd_pwr_ctrl_new_on_chip_ldo(const sd_pwr_ctrl_ldo_config_t* c,
                                              sd_pwr_ctrl_handle_t* p) {
  (void)c;
  *p = (void*)1;
  return 0;
}
static inline int sd_pwr_ctrl_del_on_chip_ldo(sd_pwr_ctrl_handle_t p) {
  (void)p;
  return 0;
}
static inline int sdmmc_host_init(void) { return 0; }
static inline int sdmmc_host_init_slot(int n, const sdmmc_slot_config_t* c) {
  (void)n;
  (void)c;
  return 0;
}
static inline int sdmmc_card_init(const sdmmc_host_t* h, sdmmc_card_t* c) {
  (void)h;
  c->csd.capacity = 65536;
  c->csd.sector_size = 512;
  return 0;
}
static inline int sdmmc_host_deinit(void) { return host_fault == 9 ? -1 : 0; }
static inline void ff_diskio_register_sdmmc(int n, sdmmc_card_t* c) {
  (void)n;
  (void)c;
}
static inline void ff_diskio_unregister(int n) { (void)n; }
static inline int esp_vfs_fat_register(const char* p, const char* d, int n,
                                       FATFS** f) {
  (void)p;
  (void)d;
  (void)n;
  static FATFS fs = {8};
  *f = &fs;
  return 0;
}
static inline int esp_vfs_fat_unregister_path(const char* p) {
  (void)p;
  return 0;
}
static inline int f_mount(FATFS* f, const char* p, int now) {
  (void)f;
  (void)p;
  (void)now;
  return host_fault == 10 ? -1 : 0;
}
static inline int f_getfree(const char* p, DWORD* n, FATFS** f) {
  (void)p;
  static FATFS fs = {8};
  *f = &fs;
  *n = host_fault == 11 ? 0 : 8192;
  return 0;
}
typedef SHA256_CTX mbedtls_sha256_context;
static inline void mbedtls_sha256_init(mbedtls_sha256_context* c) { (void)c; }
static inline void mbedtls_sha256_free(mbedtls_sha256_context* c) { (void)c; }
static inline int mbedtls_sha256_starts(mbedtls_sha256_context* c, int mode) {
  (void)mode;
  return SHA256_Init(c) ? 0 : -1;
}
static inline int mbedtls_sha256_update(mbedtls_sha256_context* c,
                                        const unsigned char* p, size_t n) {
  return SHA256_Update(c, p, n) ? 0 : -1;
}
static inline int mbedtls_sha256_finish(mbedtls_sha256_context* c,
                                        unsigned char* d) {
  return SHA256_Final(d, c) ? 0 : -1;
}
size_t host_fwrite(const void*, size_t, size_t, FILE*);
size_t host_fread(void*, size_t, size_t, FILE*);
int host_fflush(FILE*);
int host_fsync(int);
int host_fclose(FILE*);
int host_rename(const char*, const char*);
int host_unlink(const char*);
int host_ferror(FILE*);
#ifndef SDU_HOST_IMPLEMENTATION
#define fwrite host_fwrite
#define fread host_fread
#define fflush host_fflush
#define fsync host_fsync
#define fclose host_fclose
#define rename host_rename
#define unlink host_unlink
#define ferror host_ferror
#endif

#define SPECTER_SDMMC_CLK_GPIO 43
#define SPECTER_SDMMC_CMD_GPIO 44
#define SPECTER_SDMMC_D0_GPIO 39
#define SPECTER_SDMMC_D1_GPIO 40
#define SPECTER_SDMMC_D2_GPIO 41
#define SPECTER_SDMMC_D3_GPIO 42
