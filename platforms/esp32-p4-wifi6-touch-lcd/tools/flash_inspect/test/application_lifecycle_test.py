"""Run the real app_main with a normally returning core and mocked platform I/O.

This covers application handoff, not SD installation: successful upgrades block
in the platform's completion alert until a physical reset and do not return here.
"""

import ctypes
import shutil
import subprocess

import pytest

from flash_inspect.backends import PLATFORM, ROOT


@pytest.fixture(scope="module")
def application(tmp_path_factory):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("host C compiler unavailable")
    directory = tmp_path_factory.mktemp("application-lifecycle")
    (directory / "esp_system.h").write_text(
        "void esp_restart(void) __attribute__((noreturn));\n"
    )
    harness = directory / "application.c"
    harness.write_text(r"""
#include <setjmp.h>
#include <stdarg.h>
#include <string.h>
#include "bootloader.h"
#include "crc32.h"
#include "esp32p4_trailer.h"
#include "esp32p4_reset.h"
#include "esp_ota_ops.h"
#include "esp_system.h"

void app_main(void);
static jmp_buf terminal;
static esp_partition_t running;
static specter_esp32p4_role_t running_role;
static specter_boot_journal_state_t running_state;
static bool core_called, confirmation_requested;

// Model terminal platform operations without aborting the pytest process.
static void __attribute__((noreturn)) stop(int outcome) {
  longjmp(terminal, outcome);
}
#define REQUIRE(condition) do { if (!(condition)) stop(4); } while (0)

bool blsys_init(void) { return true; }
void blsys_deinit(void) {}
void blsys_fatal_error(const char *text) { (void)text; stop(3); }
const char *bootloader_status_text(bl_status_t status) {
  (void)status; return "core error";
}
const esp_partition_t *esp_ota_get_running_partition(void) { return &running; }

bool specter_esp32p4_approval_read(specter_esp32p4_role_t role,
    specter_approval_record_t *approval, bool verify_image) {
  REQUIRE(verify_image);
  REQUIRE(role == specter_role_boot_a || role == specter_role_boot_b);
  memset(approval, 0, sizeof(*approval));
  approval->role = role;
  // The other slot is always approved, newer, and unattempted. Reintroducing
  // the original inactive-slot restart path must fail the normal-handoff test.
  approval->semantic_version = role == running_role ? 100000399 : 100000599;
  approval->sequence = role == running_role ? 6 : 11;
  return true;
}
specter_boot_journal_state_t specter_esp32p4_journal_state(
    specter_esp32p4_role_t role, uint32_t sequence) {
  REQUIRE(sequence == (role == running_role ? 6 : 11));
  return role == running_role ? running_state : specter_journal_none;
}
bl_status_t bootloader_run(const bl_args_t *args, uint32_t flags) {
  REQUIRE(!core_called);
  REQUIRE(args->loaded_from == running.address);
  REQUIRE(args->struct_crc == crc32_fast(args, offsetof(bl_args_t, struct_crc), 0));
  REQUIRE(flags == (running_state == specter_journal_attempted ? bl_flag_check_only : 0));
  core_called = true;
  return bl_status_normal_exit;
}
bool specter_esp32p4_reset_request_write(specter_rtc_command_t command,
    uint32_t target, uint32_t sequence, uint32_t argument) {
  REQUIRE(core_called && running_state == specter_journal_attempted);
  REQUIRE(command == specter_rtc_confirm_bootloader && target == running_role);
  REQUIRE(sequence == 6 && argument == 0);
  confirmation_requested = true;
  return true;
}
void esp_restart(void) {
  stop(confirmation_requested ? 2 : 5);
}
bool blsys_flash_map_get_items(int items, ...) {
  REQUIRE(core_called && running_state == specter_journal_confirmed);
  REQUIRE(!confirmation_requested && items == 1);
  va_list args;
  va_start(args, items);
  int item = va_arg(args, int);
  bl_addr_t *address = va_arg(args, bl_addr_t *);
  va_end(args);
  REQUIRE(item == bl_flash_firmware_base);
  *address = 0x220000;
  return true;
}
bool blsys_start_firmware(bl_addr_t address, uint32_t argument) {
  REQUIRE(core_called && address == 0x220000 && argument == 1);
  stop(1);
}
int run_application(uint32_t role, uint32_t state) {
  running_role = role;
  running_state = state;
  core_called = confirmation_requested = false;
  running.address = role == specter_role_boot_a ? SPECTER_BOOT_A_OFFSET : SPECTER_BOOT_B_OFFSET;
  strcpy(running.label, role == specter_role_boot_a ? "boot_a" : "boot_b");
  int outcome = setjmp(terminal);
  if (outcome) return outcome;
  app_main();
  return 6; // Unexpected return instead of a terminal handoff.
}
""")
    library = directory / "application.so"
    subprocess.run(
        [
            compiler, "-shared", "-fPIC", "-DBL_NO_FATFS",
            "-D__BYTE_ORDER=1234", "-DSPECTER_BOOT_A_OFFSET=0x20000",
            "-DSPECTER_BOOT_B_OFFSET=0x120000",
            "-I", str(directory), "-I", str(ROOT / "core"),
            "-I", str(ROOT / "test"),
            "-I", str(ROOT / "test/esp32p4_stubs"),
            "-I", str(ROOT / "lib/crc32"),
            "-I", str(PLATFORM / "common"),
            str(harness), str(PLATFORM / "main/main.c"),
            str(ROOT / "core/bl_util.c"), str(ROOT / "lib/crc32/crc32.c"),
            "-o", str(library),
        ],
        check=True,
        capture_output=True,
    )
    lib = ctypes.CDLL(str(library))
    lib.run_application.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    lib.run_application.restype = ctypes.c_int
    return lib


@pytest.mark.parametrize("role", [1, 2])
def test_confirmed_fallback_reaches_main_despite_newer_unattempted_image(application, role):
    assert application.run_application(role, 0x434F4E46) == 1


@pytest.mark.parametrize("role", [1, 2])
def test_trial_requests_confirmation_before_main(application, role):
    assert application.run_application(role, 0x4154544D) == 2


@pytest.mark.parametrize("role", [1, 2])
def test_unmarked_running_image_fails_closed(application, role):
    assert application.run_application(role, 0) == 3
