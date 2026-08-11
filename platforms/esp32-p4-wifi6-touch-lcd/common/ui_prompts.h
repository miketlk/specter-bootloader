/**
 * @file ui_prompts.h
 * @brief Terminal-screen RESET prompt selection.
 */

#ifndef SPECTER_ESP32P4_UI_PROMPTS_H_INCLUDED
#define SPECTER_ESP32P4_UI_PROMPTS_H_INCLUDED

#include <stdbool.h>
#include <stdint.h>

#define SPECTER_RESET_RETRY_PROMPT "Press RESET to retry"
#define SPECTER_RESET_REBOOT_PROMPT "Press RESET to reboot"

const char* specter_esp32p4_reset_prompt(bool fatal, uint32_t time_ms);

#endif  // SPECTER_ESP32P4_UI_PROMPTS_H_INCLUDED
