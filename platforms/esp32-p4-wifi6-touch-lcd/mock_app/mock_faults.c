/**
 * @file mock_faults.c
 * @brief One-shot reset producers for plaintext hardware testing only.
 */
#include "esp32p4_test_hooks.h"

#if CONFIG_SPECTER_E2E_TEST_HOOKS
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "bootloader_common.h"
#include "crc32.h"
#include "esp32p4_boot_contract.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

void specter_mock_test_fault(void) {
  ESP_LOGI("specter-test", "MAIN reset_reason=%u",
           (unsigned)esp_reset_reason());
  rtc_retain_mem_t* initial_retained = bootloader_common_get_rtc_retain_mem();
  specter_rtc_request_t initial = {0};
  if (!initial_retained) {
    abort();
  }
  memcpy(&initial, initial_retained->custom, sizeof(initial));
  ESP_LOGI("specter-test",
           "MAIN_RETAINED raw=%08lx,%08lx,%08lx,%08lx,%08lx,%08lx,%08lx",
           (unsigned long)initial.magic, (unsigned long)initial.revision,
           (unsigned long)initial.command, (unsigned long)initial.target,
           (unsigned long)initial.sequence, (unsigned long)initial.argument,
           (unsigned long)initial.crc);
  uint32_t action = 0U;
  for (uint32_t candidate = 100U; candidate <= 116U; ++candidate) {
    if (specter_test_take(candidate, specter_role_main, 0U)) {
      action = candidate;
      break;
    }
  }
  if (!action) {
    return;
  }
  if (action == 100U) {
    esp_restart();
  }
  if (action == 101U) {
    abort();
  }
  if (action == 102U) {
    esp_task_wdt_config_t config = {
        .timeout_ms = 1000U, .idle_core_mask = 0U, .trigger_panic = true};
    ESP_ERROR_CHECK(esp_task_wdt_reconfigure(&config));
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    // Deliberately never feed this subscribed task. Idle tasks may still run.
    for (;;) {
      vTaskDelay(pdMS_TO_TICKS(100U));
    }
  }
  if (action == 103U) {
    ESP_LOGW("specter-test", "MAIN_HUNG awaiting external reset");
    for (;;) {
      vTaskDelay(pdMS_TO_TICKS(100U));
    }
  }
  if (action < 110U) {
    ESP_LOGE("specter-test", "UNSUPPORTED_ACTION");
    return;
  }
  const esp_partition_t* partition = esp_ota_get_running_partition();
  specter_approval_record_t approval;
  if (!partition || partition->address != SPECTER_MAIN_OFFSET ||
      esp_partition_read(partition, partition->size - 4096U, &approval,
                         sizeof(approval)) != ESP_OK) {
    abort();
  }
  specter_rtc_request_t request = {.magic = SPECTER_RTC_REQUEST_MAGIC,
                                   .revision = SPECTER_RTC_REQUEST_REVISION,
                                   .command = specter_rtc_boot_main,
                                   .target = specter_role_main,
                                   .sequence = approval.sequence};
  if (action == 111U) {
    --request.sequence;
  } else if (action == 112U) {
    request.target = specter_role_boot_a;
  } else if (action == 113U) {
    request.command = 0xfeedU;
  } else if (action == 114U) {
    ++request.revision;
  }
  request.crc = crc32_fast(&request, offsetof(specter_rtc_request_t, crc), 0U);
  if (action == 110U) {
    request.crc ^= 1U;
  }
  rtc_retain_mem_t* retained = bootloader_common_get_rtc_retain_mem();
  if (!retained) {
    abort();
  }
  memset(retained->custom, 0, sizeof(retained->custom));
  memcpy(retained->custom, &request, sizeof(request));
  ESP_LOGI("specter-test",
           "PRODUCED action=%lu raw=%08lx,%08lx,%08lx,%08lx,%08lx,%08lx,%08lx",
           (unsigned long)action, (unsigned long)request.magic,
           (unsigned long)request.revision, (unsigned long)request.command,
           (unsigned long)request.target, (unsigned long)request.sequence,
           (unsigned long)request.argument, (unsigned long)request.crc);
  if (action == 115U) {
    // Present a valid retained request to an externally induced reset.
    // The observer must verify what bytes actually survived that reset.
    vTaskDelay(pdMS_TO_TICKS(10000U));
    ESP_LOGE("specter-test", "EXTERNAL_RESET_TIMEOUT");
    memset(retained->custom, 0, sizeof(retained->custom));
    return;
  }
  if (action == 116U) {
    // The pinned TIMG backend configures stage 1 as a hardware system reset.
    // Expire it before the 800 ms interrupt-WDT panic can issue a SW reset.
    // This is volatile watchdog configuration, never protection or eFuse I/O.
    esp_task_wdt_config_t config = {
        .timeout_ms = 100U, .idle_core_mask = 0U, .trigger_panic = false};
    ESP_ERROR_CHECK(esp_task_wdt_reconfigure(&config));
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    portDISABLE_INTERRUPTS();
    for (;;) {
      __asm__ volatile("nop");
    }
  }
  esp_restart();
}
#endif
