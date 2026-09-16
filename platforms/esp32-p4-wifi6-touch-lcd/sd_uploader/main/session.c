/** @file session.c
 * @brief Single-owner command dispatcher and exact request replay.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "board.h"
#include "cbor.h"
#include "driver/uart.h"
#include "esp_chip_info.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "esp_private/esp_clk.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"
#include "protocol.h"
#include "storage.h"

#define BOARD SPECTER_BOARD_PROFILE
#ifndef SDU_BUILD_ID
#define SDU_BUILD_ID "unidentified"
#endif

typedef struct {
  const char* key;
  const void* data;
  uint64_t number;
  size_t length;
  char type;
} field;
static uint8_t nonce[16], owner[16], request_session[16], fingerprint[32];
static uint8_t payload[SDU_PAYLOAD] SDU_BUFFER;
static uint8_t output[SDU_FRAME] SDU_BUFFER;
static uint8_t cached[SDU_FRAME] SDU_BUFFER;
static size_t cached_size;
static uint64_t last_id, request_id;
static char opcode[16];
static bool owned, seen;
static int64_t activity;
static sdu_entry listing[8];
static size_t list_count;
static char chip[18];
static field fields[24];
static size_t field_count;
static CborError encoding_error;

static void add_text(const char* key, const char* value) {
  fields[field_count++] = (field){.key = key, .data = value, .type = 't'};
}
static void add_number(const char* key, uint64_t value) {
  fields[field_count++] = (field){.key = key, .number = value, .type = 'u'};
}
static void add_bytes(const char* key, const uint8_t* value, size_t length) {
  fields[field_count++] =
      (field){.key = key, .data = value, .length = length, .type = 'b'};
}
static int order(const void* a, const void* b) {
  const field* x = a;
  const field* y = b;
  size_t nx = strlen(x->key), ny = strlen(y->key);
  return nx == ny ? strcmp(x->key, y->key) : (nx < ny ? -1 : 1);
}
static void text(CborEncoder* enc, const char* s) {
  encoding_error |= cbor_encode_text_stringz(enc, s);
}
static void number(CborEncoder* enc, uint64_t n) {
  encoding_error |= cbor_encode_uint(enc, n);
}
static void bytes(CborEncoder* enc, const uint8_t* p, size_t n) {
  encoding_error |= cbor_encode_byte_string(enc, p, n);
}
static void encode_listing(CborEncoder* enc) {
  CborEncoder array, entry;
  encoding_error |= cbor_encoder_create_array(enc, &array, list_count);
  for (size_t i = 0; i < list_count; ++i) {
    encoding_error |= cbor_encoder_create_map(&array, &entry, 4);
    text(&entry, "name");
    text(&entry, listing[i].name);
    text(&entry, "size");
    number(&entry, listing[i].size);
    text(&entry, "type");
    text(&entry, listing[i].directory ? "directory" : "file");
    text(&entry, "temporary");
    number(&entry, listing[i].temporary);
    encoding_error |= cbor_encoder_close_container(&array, &entry);
  }
  encoding_error |= cbor_encoder_close_container(enc, &array);
}
static void reply(int status, bool event, bool cache) {
  add_text("state", sdu_state);
  add_number("offset", sdu_offset);
  qsort(fields, field_count, sizeof(field), order);
  encoding_error = CborNoError;
  CborEncoder root, map, body;
  cbor_encoder_init(&root, payload, sizeof(payload), 0);
  encoding_error |= cbor_encoder_create_map(&root, &map, 9);
  text(&map, "body");
  encoding_error |= cbor_encoder_create_map(&map, &body, field_count);
  for (size_t i = 0; i < field_count; ++i) {
    field* f = &fields[i];
    text(&body, f->key);
    if (f->type == 't')
      text(&body, f->data);
    else if (f->type == 'u')
      number(&body, f->number);
    else if (f->type == 'b')
      bytes(&body, f->data, f->length);
    else
      encode_listing(&body);
  }
  encoding_error |= cbor_encoder_close_container(&map, &body);
  text(&map, "kind");
  text(&map, event ? "event" : "response");
  text(&map, "opcode");
  text(&map, opcode);
  text(&map, "status");
  number(&map, status);
  text(&map, "service");
  text(&map, "sd-uploader");
  text(&map, "boot_nonce");
  bytes(&map, nonce, 16);
  text(&map, "request_id");
  number(&map, request_id);
  text(&map, "session_id");
  bytes(&map, request_session, 16);
  text(&map, "schema_version");
  number(&map, 1);
  encoding_error |= cbor_encoder_close_container(&root, &map);
  if (encoding_error) return;
  size_t length =
      sdu_frame(output, payload, cbor_encoder_get_buffer_size(&root, payload));
  if (cache) {
    memcpy(cached, output, length);
    cached_size = length;
  }
  uart_write_bytes(UART_NUM_0, output, length);
  field_count = 0;
}
static bool get(CborValue* map, const char* key, CborValue* value) {
  return cbor_value_is_map(map) &&
         !cbor_value_map_find_value(map, key, value) &&
         !cbor_value_at_end(value);
}
static bool string(CborValue* map, const char* key, char* out,
                   size_t capacity) {
  CborValue v;
  size_t length = capacity;
  if (!get(map, key, &v) || !cbor_value_is_text_string(&v) ||
      cbor_value_copy_text_string(&v, out, &length, NULL) || length >= capacity)
    return false;
  return strlen(out) == length;
}
static bool uint(CborValue* map, const char* key, uint64_t* out) {
  CborValue v;
  return get(map, key, &v) && cbor_value_is_unsigned_integer(&v) &&
         !cbor_value_get_uint64(&v, out);
}
static bool blob(CborValue* map, const char* key, uint8_t* out,
                 size_t* length) {
  CborValue v;
  return get(map, key, &v) && cbor_value_is_byte_string(&v) &&
         !cbor_value_copy_byte_string(&v, out, length, NULL);
}
static bool count(CborValue* map, size_t expected) {
  size_t n;
  return cbor_value_is_map(map) && !cbor_value_get_map_length(map, &n) &&
         n == expected;
}
static bool exact_blob(CborValue* map, const char* key, uint8_t* out,
                       size_t expected) {
  size_t n = expected;
  return blob(map, key, out, &n) && n == expected;
}
static void progress(uint64_t verified) {
  field_count = 0;
  add_number("verified", verified);
  reply(SDU_OK, true, false);
}
static void receipt(void) {
  add_text("name", sdu_final);
  add_number("length", sdu_length);
  add_bytes("sha256", sdu_digest, 32);
  add_number("flush_us", sdu_commit_times[0]);
  add_number("temp_verify_us", sdu_commit_times[1]);
  add_number("publish_us", sdu_commit_times[2]);
  add_number("final_verify_us", sdu_commit_times[3]);
  add_number("read_us", sdu_read_us);
  add_number("hash_us", sdu_hash_us);
}

void sdu_session_init(void) {
  esp_fill_random(nonce, sizeof(nonce));
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_BASE);
  snprintf(chip, sizeof(chip), "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1],
           mac[2], mac[3], mac[4], mac[5]);
  activity = esp_timer_get_time();
}
void sdu_session_tick(void) {
  if (!strcmp(sdu_state, "RECEIVING") &&
      esp_timer_get_time() - activity > 60000000) {
    sdu_abort();
    // Invalidate the replay receipt after inactivity cleanup.
    cached_size = 0;
  }
}
void sdu_dispatch(const uint8_t* data, size_t length) {
  int64_t started = esp_timer_get_time();
  if (!sdu_cbor(data, length)) return;
  CborParser parser;
  CborValue root, body;
  if (cbor_parser_init(data, length, 0, &parser, &root)) return;
  char service[16], kind[16];
  uint64_t schema;
  uint8_t incoming_nonce[16], zero[16] = {0};
  if (!count(&root, 8) || !string(&root, "service", service, sizeof(service)) ||
      strcmp(service, "sd-uploader") ||
      !string(&root, "kind", kind, sizeof(kind)) || strcmp(kind, "request") ||
      !string(&root, "opcode", opcode, sizeof(opcode)) ||
      !uint(&root, "schema_version", &schema) || schema != 1 ||
      !uint(&root, "request_id", &request_id) ||
      !exact_blob(&root, "boot_nonce", incoming_nonce, 16) ||
      !exact_blob(&root, "session_id", request_session, 16) ||
      !get(&root, "body", &body) || !cbor_value_is_map(&body))
    return;
  field_count = 0;
  bool hello = !strcmp(opcode, "HELLO");
  if ((hello && (memcmp(incoming_nonce, zero, 16) ||
                 !memcmp(request_session, zero, 16))) ||
      (!hello && memcmp(incoming_nonce, nonce, 16))) {
    reply(SDU_STALE, false, false);
    return;
  }
  if (owned && memcmp(request_session, owner, 16)) {
    reply(SDU_BUSY, false, false);
    return;
  }
  uint8_t digest[32];
  mbedtls_sha256(data, length, digest, 0);
  if (seen && request_id <= last_id) {
    if (request_id == last_id && !memcmp(digest, fingerprint, 32) &&
        cached_size)
      uart_write_bytes(UART_NUM_0, cached, cached_size);
    else
      reply(SDU_STALE, false, false);
    return;
  }
  if (!owned && !hello) {
    reply(SDU_STALE, false, false);
    return;
  }
  activity = esp_timer_get_time();
  int status = SDU_BAD;
  char name[129];
  uint64_t value, offset;
  if (hello && count(&body, 0)) {
    if (!owned) {
      memcpy(owner, request_session, 16);
      owned = true;
    }
    status = sdu_mount();
    // HELLO itself succeeds; mount status and state report unavailable media.
    add_number("mount_status", status);
    add_number("media_error", sdu_media_error);
    add_text("media_stage", sdu_media_stage);
    status = SDU_OK;
    add_text("board", BOARD);
    add_text("build", SDU_BUILD_ID);
    add_text("chip", chip);
    add_text("card_cid", sdu_cid);
    add_number("capacity", sdu_capacity);
    add_number("chunk_max", SDU_CHUNK);
    add_number("baud", CONFIG_SDU_UART_BAUD);
    add_number("cpu_hz", esp_clk_cpu_freq());
    add_number("file_max", INT32_MAX);
    add_text("transport", "uart");
    add_text("capabilities", "file-v1");
    esp_chip_info_t info;
    esp_chip_info(&info);
    add_number("revision", info.revision);
    add_number("heap_free", heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    add_number("stack_free", uxTaskGetStackHighWaterMark(NULL));
  } else if (!strcmp(opcode, "STATUS") && count(&body, 0)) {
    status = SDU_OK;
    if (!strcmp(sdu_state, "COMMITTED")) receipt();
  } else if (!strcmp(opcode, "BEGIN") && count(&body, 3) &&
             string(&body, "name", name, sizeof(name)) &&
             uint(&body, "length", &value) &&
             exact_blob(&body, "sha256", digest, 32)) {
    status = sdu_begin(name, value, digest, owner);
  } else if (!strcmp(opcode, "VERIFY") && count(&body, 3) &&
             string(&body, "name", name, sizeof(name)) &&
             uint(&body, "length", &value) &&
             exact_blob(&body, "sha256", digest, 32)) {
    status = sdu_verify_existing(name, value, digest, progress);
    if (!status) {
      add_text("name", name);
      add_number("length", value);
      add_bytes("sha256", digest, 32);
    }
  } else if (!strcmp(opcode, "WRITE") && count(&body, 3) &&
             uint(&body, "offset", &offset) &&
             uint(&body, "chunk_crc", &value)) {
    static uint8_t chunk[SDU_CHUNK] SDU_BUFFER;
    size_t n = sizeof(chunk);
    if (blob(&body, "data", chunk, &n) && value <= UINT32_MAX &&
        sdu_crc(chunk, n) == value)
      status = sdu_write(offset, chunk, n);
    if (!status) add_number("write_us", sdu_write_us);
  } else if (!strcmp(opcode, "COMMIT") && count(&body, 0)) {
    status = sdu_commit(progress);
    if (!status) receipt();
  } else if (!strcmp(opcode, "ABORT") && count(&body, 0))
    status = sdu_abort();
  else if (!strcmp(opcode, "RELEASE") && count(&body, 0))
    status = sdu_release();
  else if (!strcmp(opcode, "REMOVE") && count(&body, 1) &&
           string(&body, "name", name, sizeof(name)))
    status = sdu_remove(name);
  else if (!strcmp(opcode, "CLEANUP") && count(&body, 1) &&
           string(&body, "name", name, sizeof(name)))
    status = sdu_cleanup(name);
  else if (!strcmp(opcode, "LIST") && count(&body, 1) &&
           uint(&body, "cursor", &value)) {
    uint64_t next;
    status = sdu_list(value, listing, &list_count, &next);
    if (!status) {
      fields[field_count++] = (field){.key = "entries", .type = 'a'};
      add_number("next_cursor", next);
    }
  }
  if (status == SDU_DIGEST) {
    add_number("observed_length", sdu_observed_length);
    add_bytes("observed_sha256", sdu_observed_digest, 32);
  }
  last_id = request_id;
  seen = true;
  // Recompute: BEGIN/WRITE field decoding reused the local digest buffer.
  mbedtls_sha256(data, length, fingerprint, 0);
  extern uint64_t sdu_frame_cpu_us;
  add_number("frame_us", sdu_frame_cpu_us);
  add_number("operation_us", esp_timer_get_time() - started);
  reply(status, false, true);
}
