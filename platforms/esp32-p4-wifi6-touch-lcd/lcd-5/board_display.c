/*
 * SPDX-FileCopyrightText: 2026 Specter contributors
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file board_display.c
 * @brief HX8394 display, backlight, and GT911 probe for the 5-inch board.
 */

#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>

#include "board_config.h"
#include "driver/ledc.h"
#include "esp32p4_platform.h"
#include "esp_lcd_hx8394.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_ldo_regulator.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define ARRAY_SIZE(values) (sizeof(values) / sizeof((values)[0]))
#define BACKLIGHT_DUTY_MAX 1023U

static const char* TAG = "specter-board-5";
static esp_ldo_channel_handle_t dsi_ldo;
static esp_lcd_dsi_bus_handle_t dsi_bus;
static esp_lcd_panel_io_handle_t panel_io;
static esp_lcd_panel_handle_t dpi_panel;
static uint16_t* lcd_framebuffer;
static bool backlight_timer_initialized;
static bool backlight_channel_initialized;

static esp_err_t set_backlight(uint32_t percent) {
  if (percent > 100U) {
    return ESP_ERR_INVALID_ARG;
  }
  uint32_t duty = (BACKLIGHT_DUTY_MAX * percent) / 100U;
  esp_err_t result = ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
  if (ESP_OK == result) {
    result = ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
  }
  if (ESP_OK == result) {
    ESP_LOGI(TAG, "backlight=%" PRIu32 "%% duty=%" PRIu32 "/%u inverted=%u",
             percent, duty, BACKLIGHT_DUTY_MAX, SPECTER_LCD_BACKLIGHT_INVERTED);
  }
  return result;
}

static esp_err_t init_backlight(void) {
  ledc_timer_config_t timer_config = {
      .speed_mode = LEDC_LOW_SPEED_MODE,
      .duty_resolution = LEDC_TIMER_10_BIT,
      .timer_num = LEDC_TIMER_1,
      .freq_hz = 5000,
      .clk_cfg = LEDC_AUTO_CLK,
  };
  esp_err_t result = ledc_timer_config(&timer_config);
  if (ESP_OK != result) {
    return result;
  }
  backlight_timer_initialized = true;
  ledc_channel_config_t channel_config = {
      .gpio_num = SPECTER_LCD_BACKLIGHT_GPIO,
      .speed_mode = LEDC_LOW_SPEED_MODE,
      .channel = LEDC_CHANNEL_0,
      .intr_type = LEDC_INTR_DISABLE,
      .timer_sel = LEDC_TIMER_1,
      .duty = 0,
      .hpoint = 0,
      .flags.output_invert = SPECTER_LCD_BACKLIGHT_INVERTED,
  };
  result = ledc_channel_config(&channel_config);
  backlight_channel_initialized = ESP_OK == result;
  return result;
}

static esp_err_t reset_lcd(void) {
  gpio_config_t reset_config = {
      .pin_bit_mask = 1ULL << SPECTER_LCD_RESET_GPIO,
      .mode = GPIO_MODE_OUTPUT,
  };
  esp_err_t result = gpio_config(&reset_config);
  if (ESP_OK != result) {
    return result;
  }
  int active_level = SPECTER_LCD_RESET_ACTIVE_HIGH ? 1 : 0;
  result = gpio_set_level(SPECTER_LCD_RESET_GPIO, active_level);
  if (ESP_OK != result) {
    return result;
  }
  vTaskDelay(pdMS_TO_TICKS(10));
  result = gpio_set_level(SPECTER_LCD_RESET_GPIO, !active_level);
  if (ESP_OK != result) {
    return result;
  }
  vTaskDelay(pdMS_TO_TICKS(10));
  return ESP_OK;
}

static esp_err_t init_display(void) {
  esp_ldo_channel_config_t ldo_config = {
      .chan_id = SPECTER_LCD_DSI_LDO_CHANNEL,
      .voltage_mv = SPECTER_LCD_DSI_LDO_MV,
  };
  esp_err_t result = esp_ldo_acquire_channel(&ldo_config, &dsi_ldo);
  if (ESP_OK != result) {
    return result;
  }
  esp_lcd_dsi_bus_config_t bus_config = {
      .bus_id = 0,
      .num_data_lanes = SPECTER_LCD_DSI_LANES,
      .phy_clk_src = MIPI_DSI_PHY_CLK_SRC_DEFAULT,
      .lane_bit_rate_mbps = SPECTER_LCD_DSI_LANE_BITRATE_MBPS,
  };
  result = esp_lcd_new_dsi_bus(&bus_config, &dsi_bus);
  if (ESP_OK != result) {
    return result;
  }
  esp_lcd_dbi_io_config_t io_config = {
      .virtual_channel = 0,
      .lcd_cmd_bits = 8,
      .lcd_param_bits = 8,
  };
  result = esp_lcd_new_panel_io_dbi(dsi_bus, &io_config, &panel_io);
  if (ESP_OK != result) {
    return result;
  }
  esp_lcd_dpi_panel_config_t panel_config = {
      .virtual_channel = 0,
      .dpi_clk_src = MIPI_DSI_DPI_CLK_SRC_DEFAULT,
      .dpi_clock_freq_mhz = SPECTER_LCD_DPI_CLOCK_MHZ,
      .in_color_format = LCD_COLOR_FMT_RGB565,
      .num_fbs = 1,
      .video_timing =
          {
              .h_size = SPECTER_LCD_WIDTH,
              .v_size = SPECTER_LCD_HEIGHT,
              .hsync_back_porch = SPECTER_LCD_HSYNC_BACK_PORCH,
              .hsync_pulse_width = SPECTER_LCD_HSYNC_PULSE_WIDTH,
              .hsync_front_porch = SPECTER_LCD_HSYNC_FRONT_PORCH,
              .vsync_back_porch = SPECTER_LCD_VSYNC_BACK_PORCH,
              .vsync_pulse_width = SPECTER_LCD_VSYNC_PULSE_WIDTH,
              .vsync_front_porch = SPECTER_LCD_VSYNC_FRONT_PORCH,
          },
      .flags.use_dma2d = true,
  };
  result = esp_lcd_new_panel_dpi(dsi_bus, &panel_config, &dpi_panel);
  if (ESP_OK != result) {
    return result;
  }
  if (ESP_OK != (result = reset_lcd()) ||
      ESP_OK != (result = specter_hx8394_init(panel_io)) ||
      ESP_OK != (result = esp_lcd_panel_init(dpi_panel)) ||
      ESP_OK != (result = esp_lcd_dpi_panel_get_frame_buffer(
                     dpi_panel, 1, (void**)&lcd_framebuffer, (void**)NULL))) {
    return result;
  }
  ESP_LOGI(TAG,
           "HX8394 %ux%u framebuffer active: DSI=%u lanes at %u Mbps, "
           "LDO=%d/%d mV reset_active_high=%u",
           SPECTER_LCD_WIDTH, SPECTER_LCD_HEIGHT, SPECTER_LCD_DSI_LANES,
           SPECTER_LCD_DSI_LANE_BITRATE_MBPS, SPECTER_LCD_DSI_LDO_CHANNEL,
           SPECTER_LCD_DSI_LDO_MV, SPECTER_LCD_RESET_ACTIVE_HIGH);
  return ESP_OK;
}

void specter_esp32p4_board_display_deinit(void) {
  if (backlight_channel_initialized) {
    esp_err_t result = set_backlight(0);
    esp_err_t step_result =
        ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 0);
    if (ESP_OK == result) {
      result = step_result;
    }
    step_result = gpio_reset_pin(SPECTER_LCD_BACKLIGHT_GPIO);
    if (ESP_OK == result) {
      result = step_result;
    }
    step_result = gpio_set_pull_mode(SPECTER_LCD_BACKLIGHT_GPIO,
                                     GPIO_PULLDOWN_ONLY);
    if (ESP_OK == result) {
      result = step_result;
    }
    step_result =
        gpio_set_direction(SPECTER_LCD_BACKLIGHT_GPIO, GPIO_MODE_INPUT);
    if (ESP_OK == result) {
      result = step_result;
    }
    if (ESP_OK == result) {
      ESP_LOGI(TAG, "backlight deinitialized: pull=down level=%d",
               gpio_get_level(SPECTER_LCD_BACKLIGHT_GPIO));
    } else {
      ESP_LOGW(TAG, "backlight deinitialization failed: %s",
               esp_err_to_name(result));
    }
    backlight_channel_initialized = false;
  }
  if (backlight_timer_initialized) {
    ledc_timer_pause(LEDC_LOW_SPEED_MODE, LEDC_TIMER_1);
    ledc_timer_config_t timer_config = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .timer_num = LEDC_TIMER_1,
        .deconfigure = true,
    };
    ledc_timer_config(&timer_config);
    backlight_timer_initialized = false;
  }
  if (dpi_panel) {
    esp_lcd_panel_del(dpi_panel);
    dpi_panel = NULL;
    lcd_framebuffer = NULL;
  }
  if (panel_io) {
    esp_lcd_panel_io_del(panel_io);
    panel_io = NULL;
  }
  if (dsi_bus) {
    esp_lcd_del_dsi_bus(dsi_bus);
    dsi_bus = NULL;
  }
  if (dsi_ldo) {
    esp_ldo_release_channel(dsi_ldo);
    dsi_ldo = NULL;
  }
  gpio_reset_pin(SPECTER_LCD_RESET_GPIO);
}

bool specter_esp32p4_board_display_init(void) {
  ESP_LOGI(TAG, "initializing %s; camera hardware is intentionally unused",
           SPECTER_BOARD_NAME);
  esp_err_t result = init_backlight();
  if (ESP_OK == result) {
    result = set_backlight(0);
  }
  if (ESP_OK == result) {
    result = init_display();
  }
  if (ESP_OK != result) {
    ESP_LOGE(TAG, "display initialization failed: %s", esp_err_to_name(result));
    specter_esp32p4_board_display_deinit();
    return false;
  }
  return true;
}

bool specter_esp32p4_board_framebuffer(uint16_t** framebuffer,
                                      uint16_t* width, uint16_t* height) {
  if (!lcd_framebuffer || !framebuffer || !width || !height) {
    return false;
  }
  *framebuffer = lcd_framebuffer;
  *width = SPECTER_LCD_WIDTH;
  *height = SPECTER_LCD_HEIGHT;
  return true;
}

bool specter_esp32p4_board_backlight(uint8_t percent) {
  return ESP_OK == set_backlight(percent);
}

bool specter_esp32p4_board_display_enabled(bool enabled) {
  return panel_io &&
         ESP_OK == esp_lcd_panel_io_tx_param(panel_io, enabled ? 0x29 : 0x28,
                                             NULL, 0);
}

bool specter_esp32p4_board_flush(uint16_t y, uint16_t height) {
  return dpi_panel && lcd_framebuffer && y < SPECTER_LCD_HEIGHT &&
         height <= SPECTER_LCD_HEIGHT - y &&
         ESP_OK == esp_lcd_panel_draw_bitmap(
                       dpi_panel, 0, y, SPECTER_LCD_WIDTH, y + height,
                       lcd_framebuffer);
}
