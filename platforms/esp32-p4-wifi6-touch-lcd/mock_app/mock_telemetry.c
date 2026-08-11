/**
 * @file mock_telemetry.c
 * @brief TinyCBOR status encoder and resynchronizing serial frame producer.
 */

#include "mock_telemetry.h"

#include <string.h>

#include "board_config.h"
#include "cbor.h"
#include "crc32.h"
#include "driver/uart.h"
#include "esp_err.h"

#define FRAME_HEADER_SIZE 10U
#define FRAME_TRAILER_SIZE 4U
#define TELEMETRY_RX_BUFFER_SIZE 256U
#define TELEMETRY_UART UART_NUM_0

static uint8_t payload[SPECTER_MOCK_CBOR_MAX];
static uint8_t
    frame[FRAME_HEADER_SIZE + SPECTER_MOCK_CBOR_MAX + FRAME_TRAILER_SIZE];

static CborError key(CborEncoder* map, const char* name) {
  return cbor_encode_text_stringz(map, name);
}

static CborError text_value(CborEncoder* map, const char* name,
                            const char* value) {
  CborError error = key(map, name);
  return error ? error : cbor_encode_text_stringz(map, value);
}

static CborError uint_value(CborEncoder* map, const char* name,
                            uint64_t value) {
  CborError error = key(map, name);
  return error ? error : cbor_encode_uint(map, value);
}

static CborError bool_value(CborEncoder* map, const char* name, bool value) {
  CborError error = key(map, name);
  return error ? error : cbor_encode_boolean(map, value);
}

static CborError null_value(CborEncoder* map, const char* name) {
  CborError error = key(map, name);
  return error ? error : cbor_encode_null(map);
}

static CborError bytes_value(CborEncoder* map, const char* name,
                             const uint8_t* value, size_t length) {
  CborError error = key(map, name);
  return error ? error : cbor_encode_byte_string(map, value, length);
}

#define CBOR_TRY(expression)    \
  do {                          \
    error = (expression);       \
    if (CborNoError != error) { \
      return error;             \
    }                           \
  } while (0)

static CborError encode_app(CborEncoder* root,
                            const specter_mock_status_t* status) {
  CborError error;
  CborEncoder map;
  CBOR_TRY(key(root, "app"));
  CBOR_TRY(cbor_encoder_create_map(root, &map, 6));
  CBOR_TRY(text_value(&map, "name", status->app_name));
  CBOR_TRY(text_value(&map, "project_version", status->project_version));
  CBOR_TRY(text_value(&map, "idf_version", status->idf_version));
  CBOR_TRY(null_value(&map, "source_revision"));
  CBOR_TRY(uint_value(&map, "image_length", status->image_length));
  if (status->image_digest_present) {
    CBOR_TRY(bytes_value(&map, "image_digest", status->image_digest,
                         sizeof(status->image_digest)));
  } else {
    CBOR_TRY(null_value(&map, "image_digest"));
  }
  return cbor_encoder_close_container(root, &map);
}

static CborError encode_platform(CborEncoder* root,
                                 const specter_mock_status_t* status) {
  CborError error;
  CborEncoder map;
  CBOR_TRY(key(root, "platform"));
  CBOR_TRY(cbor_encoder_create_map(root, &map, 9));
  CBOR_TRY(text_value(&map, "platform_id", status->platform_id));
  CBOR_TRY(text_value(&map, "board", status->board_profile));
  CBOR_TRY(uint_value(&map, "chip_model", status->chip_model));
  CBOR_TRY(uint_value(&map, "chip_revision", status->chip_revision));
  CBOR_TRY(
      uint_value(&map, "configured_flash_size", status->configured_flash_size));
  CBOR_TRY(
      uint_value(&map, "detected_flash_size", status->detected_flash_size));
  CBOR_TRY(
      uint_value(&map, "detected_psram_size", status->detected_psram_size));
  CBOR_TRY(uint_value(&map, "display_width", status->display_width));
  CBOR_TRY(uint_value(&map, "display_height", status->display_height));
  return cbor_encoder_close_container(root, &map);
}

static CborError encode_bloat(CborEncoder* root,
                              const specter_mock_status_t* status) {
  CborError error;
  CborEncoder map;
  CBOR_TRY(key(root, "bloat"));
  CBOR_TRY(cbor_encoder_create_map(root, &map, 5));
  CBOR_TRY(uint_value(&map, "requested_bytes", status->requested_bloat));
  CBOR_TRY(uint_value(&map, "linked_bytes", status->linked_bloat));
  CBOR_TRY(text_value(&map, "section", SPECTER_MOCK_BLOAT_SECTION));
  CBOR_TRY(uint_value(&map, "fill_byte", SPECTER_MOCK_BLOAT_FILL));
  CBOR_TRY(text_value(&map, "equality_check",
                      specter_mock_check_text(status->bloat_check)));
  return cbor_encoder_close_container(root, &map);
}

static CborError encode_partition(CborEncoder* root,
                                  const specter_mock_status_t* status) {
  CborError error;
  CborEncoder map;
  CBOR_TRY(key(root, "partition"));
  CBOR_TRY(cbor_encoder_create_map(root, &map, 10));
  CBOR_TRY(text_value(&map, "label",
                      status->partition_label ? status->partition_label : ""));
  CBOR_TRY(uint_value(&map, "type", status->partition_type));
  CBOR_TRY(uint_value(&map, "subtype", status->partition_subtype));
  CBOR_TRY(uint_value(&map, "address", status->partition_address));
  CBOR_TRY(uint_value(&map, "size", status->partition_size));
  CBOR_TRY(uint_value(&map, "payload_capacity", status->payload_capacity));
  CBOR_TRY(uint_value(&map, "trailer_address", status->trailer_address));
  CBOR_TRY(uint_value(&map, "trailer_size", status->trailer_size));
  CBOR_TRY(uint_value(&map, "remaining_bytes", status->remaining_bytes));
  CBOR_TRY(bool_value(&map, "exact_main",
                      status->main_role_check == specter_mock_check_pass));
  return cbor_encoder_close_container(root, &map);
}

static CborError encode_approval(CborEncoder* root,
                                 const specter_mock_status_t* status) {
  CborError error;
  CborEncoder map;
  /* An uncommitted or corrupt trailer is untrusted binary input. Preserve its
   * explicit state, but never label arbitrary bytes as CBOR UTF-8 text. */
  const bool expose_fields =
      status->approval_state == specter_mock_approval_valid;
  CBOR_TRY(key(root, "approval"));
  CBOR_TRY(cbor_encoder_create_map(root, &map, 11));
  CBOR_TRY(text_value(
      &map, "state", specter_mock_approval_state_text(status->approval_state)));
  if (expose_fields) {
    CBOR_TRY(uint_value(&map, "revision", status->approval.revision));
    CBOR_TRY(cbor_encode_text_string(&map, "platform", 8));
    CBOR_TRY(cbor_encode_text_string(
        &map, status->approval.platform,
        strnlen(status->approval.platform, sizeof(status->approval.platform))));
    CBOR_TRY(uint_value(&map, "role", status->approval.role));
    CBOR_TRY(uint_value(&map, "semantic_version",
                        status->approval.semantic_version));
    CBOR_TRY(uint_value(&map, "image_length", status->approval.image_length));
    CBOR_TRY(uint_value(&map, "sequence", status->approval.sequence));
    CBOR_TRY(uint_value(&map, "status", status->approval.status));
    CBOR_TRY(bool_value(&map, "committed_crc",
                        status->approval_check == specter_mock_check_pass));
    CBOR_TRY(bytes_value(&map, "image_digest", status->approval.image_sha256,
                         sizeof(status->approval.image_sha256)));
  } else {
    CBOR_TRY(null_value(&map, "revision"));
    CBOR_TRY(null_value(&map, "platform"));
    CBOR_TRY(null_value(&map, "role"));
    CBOR_TRY(null_value(&map, "semantic_version"));
    CBOR_TRY(null_value(&map, "image_length"));
    CBOR_TRY(null_value(&map, "sequence"));
    CBOR_TRY(null_value(&map, "status"));
    CBOR_TRY(null_value(&map, "committed_crc"));
    CBOR_TRY(null_value(&map, "image_digest"));
  }
  CBOR_TRY(text_value(&map, "digest_check",
                      specter_mock_check_text(status->approval_digest_check)));
  return cbor_encoder_close_container(root, &map);
}

static CborError encode_checks(CborEncoder* root,
                               const specter_mock_status_t* status) {
  CborError error;
  CborEncoder map;
  CBOR_TRY(key(root, "checks"));
  CBOR_TRY(cbor_encoder_create_map(root, &map, 5));
  CBOR_TRY(text_value(&map, "main_role",
                      specter_mock_check_text(status->main_role_check)));
  CBOR_TRY(text_value(&map, "esp_image",
                      specter_mock_check_text(status->image_check)));
  CBOR_TRY(
      text_value(&map, "bloat", specter_mock_check_text(status->bloat_check)));
  CBOR_TRY(text_value(&map, "approval",
                      specter_mock_check_text(status->approval_check)));
  CBOR_TRY(text_value(&map, "approval_digest",
                      specter_mock_check_text(status->approval_digest_check)));
  return cbor_encoder_close_container(root, &map);
}

static CborError encode_status(const specter_mock_status_t* status,
                               uint32_t sequence, uint64_t uptime_ms,
                               size_t* encoded_size) {
  CborError error;
  CborEncoder encoder;
  CborEncoder root;
  CborEncoder nested;
  cbor_encoder_init(&encoder, payload, sizeof(payload), 0);
  CBOR_TRY(cbor_encoder_create_map(&encoder, &root, 14));
  CBOR_TRY(uint_value(&root, "schema_version", SPECTER_MOCK_SCHEMA_VERSION));
  CBOR_TRY(text_value(&root, "record_type", "status"));
  CBOR_TRY(uint_value(&root, "sequence", sequence));
  CBOR_TRY(uint_value(&root, "uptime_ms", uptime_ms));
  CBOR_TRY(uint_value(&root, "session_id", status->session_id));
  CBOR_TRY(encode_app(&root, status));
  CBOR_TRY(encode_platform(&root, status));
  CBOR_TRY(encode_bloat(&root, status));
  CBOR_TRY(encode_partition(&root, status));
  CBOR_TRY(encode_approval(&root, status));
  CBOR_TRY(encode_checks(&root, status));
  CBOR_TRY(key(&root, "boot"));
  CBOR_TRY(cbor_encoder_create_map(&root, &nested, 1));
  CBOR_TRY(uint_value(&nested, "reset_reason", status->reset_reason));
  CBOR_TRY(cbor_encoder_close_container(&root, &nested));
  CBOR_TRY(key(&root, "ui"));
  CBOR_TRY(cbor_encoder_create_map(&root, &nested, 2));
  CBOR_TRY(bool_value(&nested, "ready", status->ui_ready));
  if (status->ui_error) {
    CBOR_TRY(uint_value(&nested, "error_code", (uint32_t)status->ui_error));
  } else {
    CBOR_TRY(null_value(&nested, "error_code"));
  }
  CBOR_TRY(cbor_encoder_close_container(&root, &nested));
  CBOR_TRY(key(&root, "errors"));
  CBOR_TRY(cbor_encoder_create_array(&root, &nested, 0));
  CBOR_TRY(cbor_encoder_close_container(&root, &nested));
  CBOR_TRY(cbor_encoder_close_container(&encoder, &root));
  *encoded_size = cbor_encoder_get_buffer_size(&encoder, payload);
  return CborNoError;
}

bool specter_mock_telemetry_init(void) {
  const uart_config_t config = {
      .baud_rate = 115200,
      .data_bits = UART_DATA_8_BITS,
      .parity = UART_PARITY_DISABLE,
      .stop_bits = UART_STOP_BITS_1,
      .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
      .source_clk = UART_SCLK_DEFAULT,
  };
  esp_err_t result = uart_param_config(TELEMETRY_UART, &config);
  if (ESP_OK == result) {
    result =
        uart_set_pin(TELEMETRY_UART, SPECTER_UART_TX_GPIO, SPECTER_UART_RX_GPIO,
                     UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
  }
  if (ESP_OK == result) {
    result = uart_driver_install(TELEMETRY_UART, TELEMETRY_RX_BUFFER_SIZE, 0, 0,
                                 NULL, 0);
  }
  return ESP_OK == result || ESP_ERR_INVALID_STATE == result;
}

bool specter_mock_telemetry_emit(const specter_mock_status_t* status,
                                 uint32_t sequence, uint64_t uptime_ms) {
  size_t payload_size = 0U;
  if (!status ||
      CborNoError !=
          encode_status(status, sequence, uptime_ms, &payload_size) ||
      payload_size > SPECTER_MOCK_CBOR_MAX) {
    return false;
  }
  memcpy(frame, "SPMF", 4);
  frame[4] = 1U;
  frame[5] = 0U;
  frame[6] = (uint8_t)(payload_size >> 24U);
  frame[7] = (uint8_t)(payload_size >> 16U);
  frame[8] = (uint8_t)(payload_size >> 8U);
  frame[9] = (uint8_t)payload_size;
  memcpy(frame + FRAME_HEADER_SIZE, payload, payload_size);
  uint32_t checksum = crc32_fast(frame + 4U, 6U + payload_size, 0U);
  size_t checksum_offset = FRAME_HEADER_SIZE + payload_size;
  frame[checksum_offset] = (uint8_t)(checksum >> 24U);
  frame[checksum_offset + 1U] = (uint8_t)(checksum >> 16U);
  frame[checksum_offset + 2U] = (uint8_t)(checksum >> 8U);
  frame[checksum_offset + 3U] = (uint8_t)checksum;
  size_t frame_size = checksum_offset + FRAME_TRAILER_SIZE;
  return uart_write_bytes(TELEMETRY_UART, frame, frame_size) ==
             (int)frame_size &&
         ESP_OK == uart_wait_tx_done(TELEMETRY_UART, pdMS_TO_TICKS(1000U));
}
