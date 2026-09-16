#ifndef TEST_ESP_PARTITION_H_INCLUDED
#define TEST_ESP_PARTITION_H_INCLUDED

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_STATE 1

#define ESP_PARTITION_TYPE_DATA 1

typedef int esp_partition_type_t;
typedef int esp_partition_subtype_t;

typedef struct esp_partition_t {
  uint32_t address;
  uint32_t size;
  char label[17];
  bool encrypted;
} esp_partition_t;

const esp_partition_t* esp_partition_find_first(esp_partition_type_t type,
                                                esp_partition_subtype_t subtype,
                                                const char* label);
esp_err_t esp_partition_read_raw(const esp_partition_t* partition,
                                 size_t offset, void* data, size_t size);
esp_err_t esp_partition_write_raw(const esp_partition_t* partition,
                                  size_t offset, const void* data, size_t size);
esp_err_t esp_partition_read(const esp_partition_t* partition, size_t offset,
                             void* data, size_t size);
esp_err_t esp_partition_write(const esp_partition_t* partition, size_t offset,
                              const void* data, size_t size);
esp_err_t esp_partition_erase_range(const esp_partition_t* partition,
                                    size_t offset, size_t size);

#endif
