"""Compare Python policy with functions extracted verbatim from firmware C."""

import ctypes
import random
import shutil
import subprocess
import pytest
from flash_inspect import boot_policy, journal
from flash_inspect.address_space import AddressSpace
from flash_inspect.backends import PLATFORM, ROOT
from fixtures import journal_record
from policy_test import candidate


@pytest.fixture(scope="module")
def firmware(tmp_path_factory):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("host C compiler unavailable")
    source = (PLATFORM / "bootloader_components/main/bootloader_start.c").read_text()
    choose = source[
        source.index("static int choose_bootloader(") : source.index(
            "static void __attribute__((noreturn)) load_exact_partition"
        )
    ]
    source = (
        PLATFORM / "bootloader_components/main/esp32p4_root_journal.c"
    ).read_text()
    traversal = source[
        source.index(
            "specter_boot_journal_state_t specter_root_journal_state("
        ) : source.index("bool specter_root_journal_append(")
    ]
    preamble = r"""
#include <stdint.h>
#include <string.h>
#include "platforms/esp32-p4-wifi6-touch-lcd/common/esp32p4_boot_contract.h"
#include "lib/crc32/crc32.h"
#define SPECTER_JOURNAL_PARTITION_OFFSET 0
#define ESP_OK 0
static uint8_t flash[8192];
static int bootloader_flash_read(uint32_t offset, void *dest, size_t n, bool encrypted) {
  (void)encrypted;
  if (offset + n > sizeof(flash)) return -1;
  memcpy(dest, flash + offset, n);
  return 0;
}
typedef struct {
  bool approved;
  specter_approval_record_t approval;
  specter_boot_journal_state_t journal;
} specter_root_state_t;
"""
    wrappers = r"""
int select_copy(uint32_t av, uint32_t bv, uint32_t aseq, uint32_t bseq, uint32_t ast, uint32_t bst) {
  specter_root_state_t s[3] = {0};
  s[0].approved = s[1].approved = true;
  s[0].approval.semantic_version = av; s[1].approval.semantic_version = bv;
  s[0].approval.sequence = aseq; s[1].approval.sequence = bseq;
  s[0].journal = ast; s[1].journal = bst;
  return choose_bootloader(s);
}
uint32_t traverse(const void *data, uint32_t role, uint32_t seq) {
  memcpy(flash, data, sizeof(flash));
  return specter_root_journal_state(role, seq);
}
"""
    directory = tmp_path_factory.mktemp("firmware-policy")
    c = directory / "policy.c"
    c.write_text(preamble + choose + traversal + wrappers)
    library = directory / "policy.so"
    subprocess.run(
        [
            compiler,
            "-shared",
            "-fPIC",
            "-D__BYTE_ORDER=1234",
            "-I",
            str(ROOT),
            str(c),
            str(ROOT / "lib/crc32/crc32.c"),
            "-o",
            str(library),
        ],
        check=True,
        capture_output=True,
    )
    lib = ctypes.CDLL(str(library))
    lib.select_copy.argtypes = [ctypes.c_uint32] * 6
    lib.select_copy.restype = ctypes.c_int
    lib.traverse.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
    lib.traverse.restype = ctypes.c_uint32
    return lib


def test_selection_agrees_with_c(firmware):
    rng = random.Random(42)
    states = {"none": 0, "attempted": 0x4154544D, "confirmed": 0x434F4E46}
    for _ in range(200):
        av, bv, aseq, bseq = [rng.randint(1, 4) for _ in range(4)]
        astate, bstate = rng.choices(list(states), k=2)
        expected = firmware.select_copy(
            av, bv, aseq, bseq, states[astate], states[bstate]
        )
        regions = [
            candidate(1, av, aseq, astate),
            candidate(2, bv, bseq, bstate),
            candidate(3, 1, 1, "none"),
        ]
        result = boot_policy.analyze(
            regions, {"checks": {"root_policy": {"status": "pass"}}}
        )
        assert result["selection"] == (
            None if expected == -1 else f"boot_{expected + 1}"
        )


def test_journal_agrees_with_c(firmware):
    rng = random.Random(12)
    states = {0: "none", 0x4154544D: "attempted", 0x434F4E46: "confirmed"}
    options = [
        b"\xff" * 64,
        bytes(64),
        journal_record(1),
        journal_record(2),
        journal_record(1, 0x4154544D),
    ]
    for _ in range(40):
        raw = b"".join(rng.choices(options, k=128))
        for role in (1, 2):
            expected = states[firmware.traverse(raw, role, 1)]
            result = journal.inspect_sector(
                AddressSpace([(0, raw)]), (role - 1) * 4096, role, 1
            )
            assert result["effective_state"] == expected
