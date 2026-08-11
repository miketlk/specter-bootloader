#include <cstring>

#include "catch2/catch.hpp"

extern "C" {
#include "bl_syscalls.h"
#include "esp32p4_gui_test_backend.h"
#include "esp32p4_platform.h"
#include "touch_decode.h"
#include "ui_prompts.h"
}

TEST_CASE("GT911 records start at 0x814f and decode without a byte shift") {
  const uint8_t raw[SPECTER_TOUCH_GT911_POINT_BYTES] = {
      3, 0x7b, 0x00, 0xc8, 0x00, 0x34, 0x12, 0xaa};
  specter_touch_point_t point = {};
  uint8_t count = 0;
  REQUIRE(specter_touch_decode_records(raw, 1, &point, 1, 800, 480, &count));
  REQUIRE(count == 1);
  REQUIRE(point.id == 3);
  REQUIRE(point.x == 123);
  REQUIRE(point.y == 200);
  REQUIRE(point.size == 0x1234);

  const uint8_t shifted[SPECTER_TOUCH_GT911_POINT_BYTES] = {
      0, 3, 0x7b, 0x00, 0xc8, 0x00, 0x34, 0x12};
  REQUIRE_FALSE(
      specter_touch_decode_records(shifted, 1, &point, 1, 800, 480, &count));
}

TEST_CASE("GT911 decoder rejects ranges and bounds point count") {
  uint8_t raw[SPECTER_TOUCH_GT911_MAX_POINTS *
              SPECTER_TOUCH_GT911_POINT_BYTES] = {};
  specter_touch_point_t points[SPECTER_TOUCH_GT911_MAX_POINTS] = {};
  uint8_t count = 0;
  for (uint8_t index = 0; index < SPECTER_TOUCH_GT911_MAX_POINTS; ++index) {
    raw[index * SPECTER_TOUCH_GT911_POINT_BYTES] = index;
    raw[index * SPECTER_TOUCH_GT911_POINT_BYTES + 1] = index + 1;
    raw[index * SPECTER_TOUCH_GT911_POINT_BYTES + 3] = index + 2;
  }
  REQUIRE(specter_touch_decode_records(
      raw, SPECTER_TOUCH_GT911_MAX_POINTS, points,
      SPECTER_TOUCH_GT911_MAX_POINTS, 800, 480, &count));
  REQUIRE(count == SPECTER_TOUCH_GT911_MAX_POINTS);
  REQUIRE_FALSE(specter_touch_decode_records(
      raw, SPECTER_TOUCH_GT911_MAX_POINTS + 1U, points,
      SPECTER_TOUCH_GT911_MAX_POINTS, 800, 480, &count));

  raw[1] = 0x20;
  raw[2] = 0x03;  // x = 800
  REQUIRE_FALSE(specter_touch_decode_records(raw, 1, points, 1, 800, 480,
                                             &count));
}

TEST_CASE("Progress propagates rendering failures and retries transitions") {
  struct Failure {
    esp32p4_test_operation_t operation;
    unsigned occurrence;
  } failures[] = {
      {esp32p4_test_display_init, 1},
      {esp32p4_test_display_fill, 1},
      {esp32p4_test_display_enabled, 1},
      {esp32p4_test_display_backlight, 1},
      {esp32p4_test_display_fill, 2},
      {esp32p4_test_display_draw_text, 1},
      {esp32p4_test_display_draw_text, 2},
      {esp32p4_test_display_fill_rect, 1},
      {esp32p4_test_display_fill_rect, 2},
      {esp32p4_test_display_fill_rect, 3},
      {esp32p4_test_display_flush, 1},
  };

  for (const Failure& failure : failures) {
    INFO("operation=" << failure.operation
                      << " occurrence=" << failure.occurrence);
    esp32p4_test_backend_reset();
    REQUIRE(specter_esp32p4_gui_init());
    esp32p4_test_backend_fail(failure.operation, failure.occurrence);
    REQUIRE_FALSE(
        specter_esp32p4_gui_progress("Upgrade", "Writing", 5000));
    unsigned fills_after_failure =
        esp32p4_test_backend_calls(esp32p4_test_display_fill);
    esp32p4_test_backend_clear_failure();
    REQUIRE(specter_esp32p4_gui_progress("Upgrade", "Writing", 5000));
    REQUIRE(esp32p4_test_backend_calls(esp32p4_test_display_fill) >
            fills_after_failure);
    specter_esp32p4_gui_deinit();
  }
}

TEST_CASE("Hardware diagnostic propagates failures and cleans touch once") {
  struct Failure {
    esp32p4_test_operation_t operation;
    unsigned occurrence;
  } failures[] = {
      {esp32p4_test_display_fill, 2},
      {esp32p4_test_display_backlight, 2},
      {esp32p4_test_display_draw_text, 5},
      {esp32p4_test_touch_init, 1},
      {esp32p4_test_touch_read, 1},
      {esp32p4_test_touch_deinit, 1},
  };

  for (const Failure& failure : failures) {
    INFO("operation=" << failure.operation
                      << " occurrence=" << failure.occurrence);
    esp32p4_test_backend_reset();
    REQUIRE(specter_esp32p4_gui_init());
    esp32p4_test_backend_fail(failure.operation, failure.occurrence);
    REQUIRE_FALSE(specter_esp32p4_gui_hardware_diagnostic());
    if (failure.operation == esp32p4_test_touch_read ||
        failure.operation == esp32p4_test_touch_deinit) {
      REQUIRE(esp32p4_test_backend_calls(esp32p4_test_touch_deinit) == 1);
    }
    specter_esp32p4_gui_deinit();
  }

  esp32p4_test_backend_reset();
  REQUIRE(specter_esp32p4_gui_init());
  REQUIRE(specter_esp32p4_gui_hardware_diagnostic());
  REQUIRE(esp32p4_test_backend_calls(esp32p4_test_touch_deinit) == 1);
  specter_esp32p4_gui_deinit();
}

TEST_CASE("Terminal screens select accurate RESET prompts") {
  REQUIRE(std::strcmp(specter_esp32p4_reset_prompt(false, BL_FOREVER),
                      SPECTER_RESET_REBOOT_PROMPT) == 0);
  REQUIRE(std::strcmp(specter_esp32p4_reset_prompt(true, BL_FOREVER),
                      SPECTER_RESET_RETRY_PROMPT) == 0);
  REQUIRE(specter_esp32p4_reset_prompt(false, 1000) == nullptr);
}
