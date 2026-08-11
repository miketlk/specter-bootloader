#ifndef TEST_ESP_PARTITION_H_INCLUDED
#define TEST_ESP_PARTITION_H_INCLUDED

#include <stdint.h>

typedef int esp_partition_type_t;
typedef int esp_partition_subtype_t;

typedef struct esp_partition_t {
  uint32_t address;
  uint32_t size;
} esp_partition_t;

#endif
