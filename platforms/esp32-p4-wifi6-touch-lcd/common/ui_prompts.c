/**
 * @file ui_prompts.c
 * @brief Terminal-screen RESET prompt selection.
 */

#include "ui_prompts.h"

#include <stddef.h>

#include "bl_syscalls.h"

const char* specter_esp32p4_reset_prompt(bool fatal, uint32_t time_ms) {
  if (fatal) {
    return SPECTER_RESET_RETRY_PROMPT;
  }
  return BL_FOREVER == time_ms ? SPECTER_RESET_REBOOT_PROMPT : NULL;
}
