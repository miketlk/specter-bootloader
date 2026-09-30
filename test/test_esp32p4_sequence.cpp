/**
 * @file test_esp32p4_sequence.cpp
 * @brief Global approval ordering across counter loss and role changes.
 */
#include "catch2/catch.hpp"
extern "C" {
#include "esp32p4_trailer.h"
void esp_test_storage_reset(void);
void esp_test_sequence_counter(uint32_t sequence);
uint32_t esp_test_pending_sequence(specter_esp32p4_role_t role);
void esp_test_sequence_approval(specter_esp32p4_role_t role, uint32_t sequence,
                                bool committed);
extern unsigned esp_test_approval_erase_attempts;
}

TEST_CASE("Approval reservations exceed committed history in every role") {
  for (auto destination : {specter_role_boot_b, specter_role_main}) {
    for (auto maximum :
         {specter_role_boot_a, specter_role_boot_b, specter_role_main}) {
      for (uint32_t counter : {0U, 1U, 50U, 99U}) {
        esp_test_storage_reset();
        esp_test_sequence_counter(counter);
        esp_test_sequence_approval(maximum, 50U, true);
        REQUIRE(specter_esp32p4_approval_invalidate(destination));
        CHECK(esp_test_pending_sequence(destination) ==
              (counter > 50U ? counter : 50U) + 1U);
      }
    }
  }
  esp_test_storage_reset();
}

TEST_CASE(
    "Uncommitted history is ignored and exhausted history prevents erase") {
  for (auto role :
       {specter_role_boot_a, specter_role_boot_b, specter_role_main}) {
    for (bool committed : {false, true}) {
      esp_test_storage_reset();
      esp_test_sequence_approval(role, UINT32_MAX, committed);
      CHECK(specter_esp32p4_approval_invalidate(specter_role_boot_b) ==
            !committed);
      CHECK(esp_test_approval_erase_attempts == (committed ? 0U : 1U));
      CHECK(esp_test_pending_sequence(specter_role_boot_b) ==
            (committed ? 0U : 1U));
    }
  }
  esp_test_storage_reset();
  esp_test_sequence_counter(UINT32_MAX);
  CHECK_FALSE(specter_esp32p4_approval_invalidate(specter_role_boot_b));
  CHECK(esp_test_approval_erase_attempts == 0U);
  esp_test_storage_reset();
}
