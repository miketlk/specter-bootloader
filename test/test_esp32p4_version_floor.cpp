/**
 * @file test_esp32p4_version_floor.cpp
 * @brief Regression coverage for Main installation with no saved version.
 */

#include "bl_integrity_check.h"
#include "catch2/catch.hpp"
extern "C" {
#include "esp32p4_trailer.h"

bool esp_test_vcr_create(bl_addr_t address, uint32_t size, uint32_t version,
                         bl_vcr_place_t place);
uint32_t esp_test_vcr_get_version(bl_addr_t address, uint32_t size,
                                  bl_vcr_place_t place);
void esp_test_storage_reset(void);
void esp_test_storage_fail(bool fail);
unsigned esp_test_storage_writes(void);
unsigned esp_test_image_verifications(void);
}

TEST_CASE("ESP Main version floor permits first installation") {
  esp_test_storage_reset();
  const bl_addr_t address = 0x220000U;
  const uint32_t size = 0x400000U;
  const uint32_t version = 100000199U;
  SECTION("empty Main preserves an absent floor at both erase steps") {
    REQUIRE(esp_test_vcr_get_version(address, size, bl_vcr_any) ==
            BL_VERSION_NA);
    REQUIRE(esp_test_vcr_create(address, size, BL_VERSION_NA, bl_vcr_starting));
    REQUIRE(esp_test_vcr_create(address, size, BL_VERSION_NA, bl_vcr_ending));
    CHECK(esp_test_storage_writes() == 0U);
  }
  SECTION("absence and older versions cannot lower an existing floor") {
    REQUIRE(esp_test_vcr_create(address, size, version, bl_vcr_ending));
    REQUIRE(esp_test_vcr_create(address, size, BL_VERSION_NA, bl_vcr_starting));
    REQUIRE(esp_test_vcr_create(address, size, version - 100U, bl_vcr_ending));
    CHECK(esp_test_vcr_get_version(address, size, bl_vcr_any) == version);
    CHECK(esp_test_storage_writes() == 1U);
  }
  SECTION("invalid version and destination still fail") {
    CHECK_FALSE(
        esp_test_vcr_create(address, size, BL_VERSION_MAX + 1U, bl_vcr_ending));
    CHECK_FALSE(esp_test_vcr_create(0U, size, BL_VERSION_NA, bl_vcr_ending));
    CHECK_FALSE(esp_test_vcr_create(address, size, BL_VERSION_NA, bl_vcr_any));
    CHECK(esp_test_storage_writes() == 0U);
  }
  SECTION("storage failure cannot be mistaken for an absent floor") {
    esp_test_storage_fail(true);
    CHECK_FALSE(
        esp_test_vcr_create(address, size, BL_VERSION_NA, bl_vcr_starting));
  }
  SECTION("version zero remains forbidden for candidate approval") {
    uint8_t digest[32] = {};
    CHECK_FALSE(specter_esp32p4_approval_create_authorized(
        specter_role_main, 1024U, BL_VERSION_NA, digest));
    CHECK(esp_test_image_verifications() == 0U);
    CHECK(esp_test_storage_writes() == 0U);
    // A valid version reaches the image verifier (which this test stubs to
    // fail).
    CHECK_FALSE(specter_esp32p4_approval_create_authorized(
        specter_role_main, 1024U, version, digest));
    CHECK(esp_test_image_verifications() == 1U);
  }
  esp_test_storage_reset();
}
