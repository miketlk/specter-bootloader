/**
 * @file esp_lcd_hx8394.h
 * @brief Minimal Waveshare HX8394 MIPI-DSI command interface.
 */

#ifndef SPECTER_ESP_LCD_HX8394_H_INCLUDED
#define SPECTER_ESP_LCD_HX8394_H_INCLUDED

#include "esp_err.h"
#include "esp_lcd_panel_io.h"

/// Sends the Waveshare 5-inch HX8394 initialization sequence.
esp_err_t specter_hx8394_init(esp_lcd_panel_io_handle_t panel_io);

#endif  // SPECTER_ESP_LCD_HX8394_H_INCLUDED
