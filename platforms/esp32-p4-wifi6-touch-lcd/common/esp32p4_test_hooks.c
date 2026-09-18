/**
 * @file esp32p4_test_hooks.c
 * @brief One-shot hardware-test pause controlled by a reserved fixture sector.
 */
#include "esp32p4_test_hooks.h"

#if CONFIG_SPECTER_E2E_TEST_HOOKS
#include <stddef.h>

#include "crc32.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#ifdef BOOTLOADER_BUILD
#include "bootloader_flash_priv.h"
#include "hal/wdt_hal.h"
#else
#include "esp_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif

#if CONFIG_SECURE_BOOT || CONFIG_SECURE_FLASH_ENC_ENABLED
#error "E2E hooks require an unprotected plaintext development fixture"
#endif

// Outside all production partitions. The harness owns and restores this
// sector explicitly. Stock builds contain no accesses to this address.
#define TEST_CONTROL_OFFSET 0x622000U

typedef struct {
  uint32_t magic;
  uint32_t hook;
  uint32_t role;
  uint32_t progress;
  uint32_t crc;
  uint32_t armed;
  uint32_t reserved[2];
} test_control_t;

static bool take_at(uint32_t address, uint32_t hook, uint32_t role,
                    uint32_t progress) {
  test_control_t control;
#ifdef BOOTLOADER_BUILD
  esp_err_t result =
      bootloader_flash_read(address, &control, sizeof(control), false);
#else
  esp_err_t result = esp_flash_read(NULL, &control, address, sizeof(control));
#endif
  if (result != ESP_OK || control.magic != 0x45325034U ||
      control.hook != hook || control.role != role ||
      progress < control.progress || control.armed != UINT32_MAX ||
      control.crc != crc32_fast(&control, offsetof(test_control_t, crc), 0U)) {
    return false;
  }
  // Commit consumption before announcing the boundary; a reset cannot
  // rearm the fault. Never modify an approval, journal, floor, or eFuse here.
  control.armed = 0U;
#ifdef BOOTLOADER_BUILD
  result = bootloader_flash_write(address + 16U, (uint8_t*)&control + 16U, 16U,
                                  false);
#else
  result = esp_flash_write(NULL, (uint8_t*)&control + 16U, address + 16U, 16U);
#endif
  if (result != ESP_OK) {
    ESP_LOGE("specter-test", "HOOK_IO_ERROR");
    return false;
  }
  ESP_LOGW("specter-test", "HOOK id=%lu role=%lu progress=%lu consumed=1",
           (unsigned long)hook, (unsigned long)role, (unsigned long)progress);
  return true;
}

bool specter_test_take(uint32_t hook, uint32_t role, uint32_t progress) {
  // Two independent one-shot records permit a setup handoff followed by a
  // Main fault, without a ROM/uploader session between the live resets.
  return take_at(TEST_CONTROL_OFFSET, hook, role, progress) ||
         take_at(TEST_CONTROL_OFFSET + sizeof(test_control_t), hook, role,
                 progress);
}

void specter_test_boundary(uint32_t hook, uint32_t role, uint32_t progress) {
  if (!specter_test_take(hook, role, progress)) {
    return;
  }
  for (unsigned i = 0; i < 100U; ++i) {
#ifdef BOOTLOADER_BUILD
    wdt_hal_context_t context = RWDT_HAL_CONTEXT_DEFAULT();
    wdt_hal_write_protect_disable(&context);
    wdt_hal_feed(&context);
    wdt_hal_write_protect_enable(&context);
    esp_rom_delay_us(100000U);
#else
    vTaskDelay(pdMS_TO_TICKS(100U));
#endif
  }
  ESP_LOGE("specter-test", "HOOK_TIMEOUT id=%lu", (unsigned long)hook);
}
#endif
