/**
 * @file esp32p4_platform.h
 * @brief Shared fixed-role definitions for Waveshare ESP32-P4 boards.
 */

#ifndef ESP32P4_PLATFORM_H_INCLUDED
#define ESP32P4_PLATFORM_H_INCLUDED

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp32p4_boot_contract.h"
#include "esp_partition.h"

#define SPECTER_ESP32P4_MEDIA_MOUNT_POINT "/sdcard"

typedef struct specter_esp32p4_fixed_partition {
  const char* label;
  esp_partition_type_t type;
  esp_partition_subtype_t subtype;
  uint32_t address;
  uint32_t size;
  specter_esp32p4_role_t role;
} specter_esp32p4_fixed_partition_t;

/// Returns the exact three-entry compiled partition allow-list.
const specter_esp32p4_fixed_partition_t* specter_esp32p4_fixed_layout(
    size_t* count);

/// Resolves and validates every compiled fixed-role partition.
bool specter_esp32p4_flash_map_init(void);

/// Returns a validated fixed-role partition by role.
const esp_partition_t* specter_esp32p4_partition(specter_esp32p4_role_t role);

/// Resolves a complete absolute flash range to one fixed-role partition.
const esp_partition_t* specter_esp32p4_partition_for_range(
    uintptr_t address, size_t size, size_t* relative_offset,
    specter_esp32p4_role_t* role);

/// Returns the role corresponding to an exact fixed partition base.
specter_esp32p4_role_t specter_esp32p4_role_for_base(uintptr_t address);

/// Reports whether the role's trailer was erased before candidate mutation.
bool specter_esp32p4_candidate_prepared(specter_esp32p4_role_t role);

/// Closes the candidate mutation transaction after its approval commit.
void specter_esp32p4_candidate_committed(specter_esp32p4_role_t role);

/// Returns the configured board platform identifier.
const char* specter_esp32p4_platform_id(void);

/// Initializes the selected board's panel and makes its framebuffer available.
bool specter_esp32p4_board_display_init(void);

/// Releases resources acquired by specter_esp32p4_board_display_init().
void specter_esp32p4_board_display_deinit(void);

/// Returns the selected panel's RGB565 framebuffer and geometry.
bool specter_esp32p4_board_framebuffer(uint16_t** framebuffer, uint16_t* width,
                                       uint16_t* height);

/// Controls the board-local backlight and panel output.
bool specter_esp32p4_board_backlight(uint8_t percent);
bool specter_esp32p4_board_display_enabled(bool enabled);

/// Flushes a changed horizontal framebuffer band to the DPI engine.
bool specter_esp32p4_board_flush(uint16_t y, uint16_t height);

/// Initializes board-independent GUI state without eagerly starting the LCD.
bool specter_esp32p4_gui_init(void);

/// Releases board-independent GUI forwarding resources.
void specter_esp32p4_gui_deinit(void);

/// Emits an alert through the current GUI/log forwarding layer.
bool specter_esp32p4_gui_alert(int type, const char* caption, const char* text,
                               const char* user_action);

/// Emits upgrade progress through the current GUI/log forwarding layer.
bool specter_esp32p4_gui_progress(const char* caption, const char* operation,
                                  uint32_t percent_x100);

/// Runs the opt-in real-board Phase 6 diagnostic.
bool specter_esp32p4_gui_hardware_diagnostic(void);

#endif  // ESP32P4_PLATFORM_H_INCLUDED
