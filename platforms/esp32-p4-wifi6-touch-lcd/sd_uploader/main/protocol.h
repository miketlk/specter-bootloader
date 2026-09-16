/** @file protocol.h
 * @brief Bounded SPSD framing and deterministic CBOR validation.
 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define SDU_CHUNK 16384
#define SDU_PAYLOAD 18432
#define SDU_FRAME (SDU_PAYLOAD + 14)
uint32_t sdu_crc(const uint8_t* data, size_t length);
bool sdu_cbor(const uint8_t* data, size_t length);
/// Feed one byte. A complete accepted frame is returned without its envelope.
bool sdu_feed(uint8_t byte, int64_t now, const uint8_t** data, size_t* length);
size_t sdu_frame(uint8_t* out, const uint8_t* payload, size_t length);
bool sdu_name(const char* name);
#ifdef ESP_PLATFORM
#define SDU_BUFFER __attribute__((section(".sdu_buffers")))
#else
#define SDU_BUFFER
#endif
