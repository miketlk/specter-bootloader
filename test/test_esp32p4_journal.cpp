/**
 * @file test_esp32p4_journal.cpp
 * @brief Shared plaintext journal isolation and interrupted-write tests.
 */

#include <cstring>

#include "catch2/catch.hpp"
extern "C" {
#include "../platforms/esp32-p4-wifi6-touch-lcd/bootloader_components/main/esp32p4_root_journal.h"
#include "esp32p4_trailer.h"
void esp_test_storage_reset(void);
void esp_test_journal_encrypted(bool encrypted);
extern uint8_t esp_test_journal_data[0x2000];
extern int esp_test_journal_write_budget;
extern bool esp_test_fail_approval_erase;
extern unsigned esp_test_approval_erase_attempts;
extern unsigned esp_test_journal_erase_attempts;
}

TEST_CASE("Shared Bootloader journal keeps each slot independent") {
  esp_test_storage_reset();
  const auto a = specter_role_boot_a;
  const auto b = specter_role_boot_b;
  REQUIRE(specter_root_journal_append(a, 7, specter_journal_confirmed));
  REQUIRE(specter_root_journal_append(b, 7, specter_journal_attempted));
  CHECK(specter_esp32p4_journal_state(a, 7) == specter_journal_confirmed);
  CHECK(specter_root_journal_state(a, 7) == specter_journal_confirmed);
  CHECK(specter_esp32p4_journal_state(b, 7) == specter_journal_attempted);
  CHECK(specter_root_journal_state(b, 7) == specter_journal_attempted);
  CHECK(specter_esp32p4_journal_state(b, 8) == specter_journal_none);

  SECTION("updating inactive slot erases only its journal after approval") {
    REQUIRE(specter_esp32p4_approval_invalidate(b));
    CHECK(specter_esp32p4_journal_state(a, 7) == specter_journal_confirmed);
    CHECK(specter_root_journal_state(a, 7) == specter_journal_confirmed);
    CHECK(specter_esp32p4_journal_state(b, 7) == specter_journal_none);
    REQUIRE(specter_root_journal_append(b, 8, specter_journal_attempted));
    REQUIRE(specter_root_journal_append(b, 8, specter_journal_confirmed));
    CHECK(specter_esp32p4_journal_state(b, 8) == specter_journal_confirmed);
    CHECK(specter_root_journal_state(b, 8) == specter_journal_confirmed);
  }
  SECTION("failed approval erase preserves journal state") {
    esp_test_fail_approval_erase = true;
    CHECK_FALSE(specter_esp32p4_approval_invalidate(b));
    CHECK(esp_test_approval_erase_attempts == 1U);
    CHECK(esp_test_journal_erase_attempts == 0U);
    CHECK(specter_esp32p4_journal_state(a, 7) == specter_journal_confirmed);
    CHECK(specter_root_journal_state(a, 7) == specter_journal_confirmed);
    CHECK(specter_esp32p4_journal_state(b, 7) == specter_journal_attempted);
    CHECK(specter_root_journal_state(b, 7) == specter_journal_attempted);
  }
  SECTION("running slot is rejected before any erase with healthy storage") {
    CHECK_FALSE(specter_esp32p4_approval_invalidate(a));
    CHECK(esp_test_approval_erase_attempts == 0U);
    CHECK(esp_test_journal_erase_attempts == 0U);
    CHECK(specter_esp32p4_journal_state(a, 7) == specter_journal_confirmed);
    CHECK(specter_root_journal_state(a, 7) == specter_journal_confirmed);
    CHECK(specter_esp32p4_journal_state(b, 7) == specter_journal_attempted);
    CHECK(specter_root_journal_state(b, 7) == specter_journal_attempted);
  }
  SECTION("encrypted journal partition is rejected") {
    esp_test_journal_encrypted(true);
    CHECK(specter_esp32p4_journal_partition() == nullptr);
    CHECK(specter_esp32p4_journal_state(b, 7) == specter_journal_none);
  }
  esp_test_storage_reset();
}

TEST_CASE("Journal append skips torn slots and cannot cross sector boundary") {
  esp_test_storage_reset();
  const auto b = specter_role_boot_b;
  SECTION("uncommitted confirmation does not confirm the trial") {
    REQUIRE(specter_root_journal_append(b, 2, specter_journal_attempted));
    esp_test_journal_write_budget = 1;
    CHECK_FALSE(specter_root_journal_append(b, 2, specter_journal_confirmed));
    CHECK(specter_esp32p4_journal_state(b, 2) == specter_journal_attempted);
    CHECK(specter_root_journal_state(b, 2) == specter_journal_attempted);
    esp_test_journal_write_budget = -1;
    REQUIRE(specter_root_journal_append(b, 2, specter_journal_confirmed));
    CHECK(specter_esp32p4_journal_state(b, 2) == specter_journal_confirmed);
    CHECK(specter_root_journal_state(b, 2) == specter_journal_confirmed);
  }
  SECTION("erased magic alone does not make a torn record writable") {
    esp_test_journal_data[0x1000 + 20] = 0;
    REQUIRE(specter_root_journal_append(b, 2, specter_journal_attempted));
    CHECK(esp_test_journal_data[0x1000] == 0xff);
    CHECK(specter_esp32p4_journal_state(b, 2) == specter_journal_attempted);
    CHECK(specter_root_journal_state(b, 2) == specter_journal_attempted);
  }
  SECTION("full first sector cannot overwrite second slot") {
    REQUIRE(specter_root_journal_append(b, 2, specter_journal_confirmed));
    memset(esp_test_journal_data, 0, 0x1000);
    CHECK_FALSE(specter_root_journal_append(specter_role_boot_a, 3,
                                            specter_journal_attempted));
    CHECK(specter_esp32p4_journal_state(b, 2) == specter_journal_confirmed);
    CHECK(specter_root_journal_state(b, 2) == specter_journal_confirmed);
  }
  esp_test_storage_reset();
}
