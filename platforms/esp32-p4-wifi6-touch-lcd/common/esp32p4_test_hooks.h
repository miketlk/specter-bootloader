/**
 * @file esp32p4_test_hooks.h
 * @brief Disabled-by-default, one-shot development fault boundaries.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#endif

enum {
  specter_test_reserved = 1,
  specter_test_invalidated,
  specter_test_erased,
  specter_test_copied,
  specter_test_validated,
  specter_test_floor,
  specter_test_approval_prefix,
  specter_test_approval_commit,
  specter_test_attempt_before,
  specter_test_attempt_prefix,
  specter_test_attempt_commit,
  specter_test_confirm_before,
  specter_test_confirm_prefix,
  specter_test_confirm_commit,
  specter_test_app_entry,
};

#if CONFIG_SPECTER_E2E_TEST_HOOKS
/// Consume and announce a matching control record without pausing.
bool specter_test_take(uint32_t hook, uint32_t role, uint32_t progress);

/// Consume a matching flash fixture once, then pause for an external reset.
void specter_test_boundary(uint32_t hook, uint32_t role, uint32_t progress);
#else
static inline void specter_test_boundary(uint32_t hook, uint32_t role,
                                         uint32_t progress) {
  (void)hook;
  (void)role;
  (void)progress;
}
#endif
