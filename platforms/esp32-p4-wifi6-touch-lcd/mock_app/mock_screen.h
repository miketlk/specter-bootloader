/**
 * @file mock_screen.h
 * @brief Static welcome screen for the mock Main Firmware.
 */

#ifndef SPECTER_MOCK_SCREEN_H_INCLUDED
#define SPECTER_MOCK_SCREEN_H_INCLUDED

#include <stdbool.h>

#include "mock_status.h"

bool specter_mock_screen_render(const specter_mock_status_t* status);

#endif  // SPECTER_MOCK_SCREEN_H_INCLUDED
