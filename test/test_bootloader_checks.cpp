/**
 * @file test_bootloader_checks.cpp
 * @brief Regression tests for the non-updating Bootloader initialization path.
 */

#include "bootloader.h"
#include "catch2/catch.hpp"
#include "crc32.h"

extern "C" {
extern size_t flash_emu_size;
extern unsigned blsys_test_erase_calls;
extern unsigned blsys_test_write_calls;
extern unsigned blsys_test_find_calls;
extern unsigned blsys_test_media_calls;
extern unsigned blsys_test_progress_calls;
extern bool blsys_test_media_present;
}

TEST_CASE("Bootloader trial checks cannot enter the updater") {
  bl_args_t args = {};
  args.loaded_from = 0x081E0000U;
  args.struct_crc = crc32_fast(&args, offsetof(bl_args_t, struct_crc), 0U);
  flash_emu_size = 2U * 1024U * 1024U;
  blsys_test_erase_calls = blsys_test_write_calls = blsys_test_find_calls = 0U;
  blsys_test_media_calls = blsys_test_progress_calls = 0U;
  blsys_test_media_present = GENERATE(false, true);

  SECTION("trial initialization succeeds with or without removable media") {
    CHECK(bootloader_run(&args, bl_flag_check_only) == bl_status_normal_exit);
    CHECK(blsys_test_media_calls == 1U);
    CHECK(blsys_test_progress_calls > 0U);
  }
  SECTION("invalid arguments cannot pass trial initialization") {
    args.struct_crc ^= 1U;
    CHECK(bootloader_run(&args, bl_flag_check_only) == bl_status_err_arg);
    CHECK(blsys_test_media_calls == 0U);
    CHECK(blsys_test_progress_calls == 0U);
  }
  CHECK(blsys_test_find_calls == 0U);
  CHECK(blsys_test_erase_calls == 0U);
  CHECK(blsys_test_write_calls == 0U);
  flash_emu_size = 0U;
  blsys_test_media_present = true;
}
