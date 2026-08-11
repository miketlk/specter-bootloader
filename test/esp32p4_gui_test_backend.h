#ifndef ESP32P4_GUI_TEST_BACKEND_H_INCLUDED
#define ESP32P4_GUI_TEST_BACKEND_H_INCLUDED

typedef enum esp32p4_test_operation {
  esp32p4_test_display_init,
  esp32p4_test_display_fill,
  esp32p4_test_display_fill_rect,
  esp32p4_test_display_draw_text,
  esp32p4_test_display_flush,
  esp32p4_test_display_backlight,
  esp32p4_test_display_enabled,
  esp32p4_test_touch_init,
  esp32p4_test_touch_read,
  esp32p4_test_touch_deinit,
  esp32p4_test_operation_count,
} esp32p4_test_operation_t;

void esp32p4_test_backend_reset(void);
void esp32p4_test_backend_fail(esp32p4_test_operation_t operation,
                               unsigned occurrence);
void esp32p4_test_backend_clear_failure(void);
unsigned esp32p4_test_backend_calls(esp32p4_test_operation_t operation);
unsigned esp32p4_test_backend_display_deinits(void);

#endif
