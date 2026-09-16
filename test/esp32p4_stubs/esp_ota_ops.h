/** @file esp_ota_ops.h
 * @brief Host running-partition API subset.
 */
#ifndef TEST_ESP_OTA_OPS_H_INCLUDED
#define TEST_ESP_OTA_OPS_H_INCLUDED

#include "esp_partition.h"

const esp_partition_t* esp_ota_get_running_partition(void);

#endif
