/** @file nvs.h
 * @brief Host NVS API subset for approval transaction tests.
 */
#ifndef TEST_NVS_H_INCLUDED
#define TEST_NVS_H_INCLUDED

#include "esp_partition.h"

typedef unsigned nvs_handle_t;
#define NVS_READWRITE 1
#define NVS_READONLY 0
#define ESP_ERR_NVS_NOT_FOUND 2

esp_err_t nvs_open(const char* name, int mode, nvs_handle_t* handle);
esp_err_t nvs_get_u32(nvs_handle_t handle, const char* key, uint32_t* value);
esp_err_t nvs_set_u32(nvs_handle_t handle, const char* key, uint32_t value);
esp_err_t nvs_commit(nvs_handle_t handle);
void nvs_close(nvs_handle_t handle);

#endif
