/** @file esp_image_format.h
 * @brief Host image verification API subset for approval transaction tests.
 */
#ifndef TEST_ESP_IMAGE_FORMAT_H_INCLUDED
#define TEST_ESP_IMAGE_FORMAT_H_INCLUDED

#include "esp_partition.h"

#define ESP_IMAGE_VERIFY 0
#define ESP_IMAGE_VERIFY_SILENT 1
typedef struct {
  uint32_t offset;
  uint32_t size;
} esp_partition_pos_t;
typedef struct {
  struct {
    uint8_t hash_appended;
  } image;
  uint32_t image_len;
  uint8_t image_digest[32];
} esp_image_metadata_t;

esp_err_t esp_image_verify(int mode, const esp_partition_pos_t* position,
                           esp_image_metadata_t* metadata);

#endif
