/** @file board.h
 * @brief Reuse board wiring constants without linking display code.
 */
#pragma once
#include "sdkconfig.h"
#if CONFIG_SDU_BOARD_4P3
#include "../../lcd-4p3/board_config.h"
#elif CONFIG_SDU_BOARD_5
#include "../../lcd-5/board_config.h"
#else
#error "Unsupported SD uploader board"
#endif
