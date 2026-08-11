/**
 * @file mock_telemetry.h
 * @brief Framed CBOR telemetry for the ESP32-P4 mock Main Firmware.
 */

#ifndef SPECTER_MOCK_TELEMETRY_H_INCLUDED
#define SPECTER_MOCK_TELEMETRY_H_INCLUDED

#include <stdbool.h>
#include <stdint.h>

#include "mock_status.h"

bool specter_mock_telemetry_init(void);
bool specter_mock_telemetry_emit(const specter_mock_status_t* status,
                                 uint32_t sequence, uint64_t uptime_ms);

#endif  // SPECTER_MOCK_TELEMETRY_H_INCLUDED
