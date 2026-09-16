/**
 * @file bootloader_flash_priv.h
 * @brief Host declarations for Root Loader flash I/O.
 */

#ifndef TEST_BOOTLOADER_FLASH_PRIV_H_INCLUDED
#define TEST_BOOTLOADER_FLASH_PRIV_H_INCLUDED

#include "esp_partition.h"

esp_err_t bootloader_flash_read(size_t address, void* data, size_t size,
                                bool allow_decrypt);
esp_err_t bootloader_flash_write(size_t address, void* data, size_t size,
                                 bool write_encrypted);

#endif  // TEST_BOOTLOADER_FLASH_PRIV_H_INCLUDED
