/**
 * @file touch_hal.c
 * @brief Minimal GT911 register driver for bootloader diagnostics.
 */

#include "touch_hal.h"

#include <stddef.h>

#include "board_config.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define ARRAY_SIZE(values) (sizeof(values) / sizeof((values)[0]))
#define GT911_PRODUCT_ID_REGISTER 0x8140U
#define GT911_STATUS_REGISTER 0x814eU
#define GT911_POINT_REGISTER 0x814fU

static const char* TAG = "specter-touch";
static i2c_master_bus_handle_t bus;
static i2c_master_dev_handle_t device;

static bool read_register(uint16_t address, void* data, size_t size) {
  uint8_t request[] = {(uint8_t)(address >> 8U), (uint8_t)address};
  return device && ESP_OK == i2c_master_transmit_receive(
                                 device, request, sizeof(request), data, size,
                                 100);
}

static bool write_register(uint16_t address, uint8_t value) {
  uint8_t request[] = {(uint8_t)(address >> 8U), (uint8_t)address, value};
  return device && ESP_OK == i2c_master_transmit(device, request,
                                                 sizeof(request),
                                                 100);
}

bool specter_touch_init(void) {
  if (device) {
    return true;
  }
#if SPECTER_TOUCH_HAS_RESET
  gpio_config_t reset_config = {
      .pin_bit_mask = 1ULL << SPECTER_TOUCH_RESET_GPIO,
      .mode = GPIO_MODE_OUTPUT,
  };
  if (ESP_OK != gpio_config(&reset_config) ||
      ESP_OK != gpio_set_level(SPECTER_TOUCH_RESET_GPIO, 0)) {
    (void)specter_touch_deinit();
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(10));
  if (ESP_OK != gpio_set_level(SPECTER_TOUCH_RESET_GPIO, 1)) {
    (void)specter_touch_deinit();
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(50));
#endif
  i2c_master_bus_config_t bus_config = {
      .i2c_port = SPECTER_TOUCH_I2C_PORT,
      .sda_io_num = SPECTER_TOUCH_I2C_SDA_GPIO,
      .scl_io_num = SPECTER_TOUCH_I2C_SCL_GPIO,
      .clk_source = I2C_CLK_SRC_DEFAULT,
      .glitch_ignore_cnt = 7,
      .flags.enable_internal_pullup = true,
  };
  if (ESP_OK != i2c_new_master_bus(&bus_config, &bus)) {
    (void)specter_touch_deinit();
    return false;
  }
  const uint8_t addresses[] = {SPECTER_TOUCH_GT911_ADDRESS,
                               SPECTER_TOUCH_GT911_BACKUP_ADDRESS};
  uint8_t selected = 0;
  for (size_t index = 0; index < ARRAY_SIZE(addresses); ++index) {
    if (ESP_OK == i2c_master_probe(bus, addresses[index], 100)) {
      selected = addresses[index];
      break;
    }
  }
  i2c_device_config_t device_config = {
      .dev_addr_length = I2C_ADDR_BIT_LEN_7,
      .device_address = selected,
      .scl_speed_hz = SPECTER_TOUCH_I2C_FREQUENCY_HZ,
  };
  if (!selected || ESP_OK != i2c_master_bus_add_device(bus, &device_config,
                                                       &device)) {
    (void)specter_touch_deinit();
    return false;
  }
  char product_id[5] = {0};
  if (!read_register(GT911_PRODUCT_ID_REGISTER, product_id, 4)) {
    (void)specter_touch_deinit();
    return false;
  }
  ESP_LOGI(TAG, "GT911 product=%s address=0x%02x coordinates=%ux%u",
           product_id, selected, SPECTER_LCD_WIDTH, SPECTER_LCD_HEIGHT);
  return true;
}

bool specter_touch_deinit(void) {
  bool ok = true;
  if (device) {
    ok = ESP_OK == i2c_master_bus_rm_device(device) && ok;
    device = NULL;
  }
  if (bus) {
    ok = ESP_OK == i2c_del_master_bus(bus) && ok;
    bus = NULL;
  }
#if SPECTER_TOUCH_HAS_RESET
  ok = ESP_OK == gpio_reset_pin(SPECTER_TOUCH_RESET_GPIO) && ok;
#endif
  return ok;
}

bool specter_touch_read(specter_touch_point_t* points, uint8_t capacity,
                        uint8_t* count) {
  if (!device || !points || !capacity || !count) {
    return false;
  }
  uint8_t status = 0;
  if (!read_register(GT911_STATUS_REGISTER, &status, sizeof(status))) {
    return false;
  }
  *count = 0;
  if (!(status & 0x80U)) {
    return true;
  }
  uint8_t available = status & 0x0fU;
  if (available > SPECTER_TOUCH_GT911_MAX_POINTS) {
    (void)write_register(GT911_STATUS_REGISTER, 0);
    return false;
  }
  /*
   * GT911's coordinate status is 0x814e and the first complete eight-byte
   * record begins immediately at 0x814f. Espressif component 1.2.0~2 and the
   * GT911 register map agree on this layout. Read one contiguous diagnostic
   * snapshot so the leading byte and record stride remain visible in logs.
   */
  uint8_t raw[1U + SPECTER_TOUCH_GT911_MAX_POINTS *
                       SPECTER_TOUCH_GT911_POINT_BYTES];
  size_t raw_size =
      1U + (size_t)available * SPECTER_TOUCH_GT911_POINT_BYTES;
  if (!read_register(GT911_STATUS_REGISTER, raw, raw_size)) {
    return false;
  }
#if CONFIG_SPECTER_UI_HARDWARE_DIAGNOSTIC
  ESP_LOGI(TAG, "raw snapshot 0x814e..0x%04x (%u point%s)",
           (unsigned)(GT911_STATUS_REGISTER + raw_size - 1U), available,
           1U == available ? "" : "s");
  ESP_LOG_BUFFER_HEX(TAG, raw, raw_size);
#endif
  uint8_t snapshot_count = raw[0] & 0x0fU;
  bool ok = (raw[0] & 0x80U) && snapshot_count == available &&
            specter_touch_decode_records(
                raw + (GT911_POINT_REGISTER - GT911_STATUS_REGISTER),
                snapshot_count, points, capacity, SPECTER_LCD_WIDTH,
                SPECTER_LCD_HEIGHT, count);
  if (!ok) {
    ESP_LOGW(TAG, "invalid GT911 point snapshot status=0x%02x", raw[0]);
    *count = 0;
  }
  return write_register(GT911_STATUS_REGISTER, 0) && ok;
}
