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
        source.index("static int choose_bootloader("): source.index(
            "static void __attribute__((noreturn)) load_exact_partition"
        )
    ]
    source = (
        PLATFORM / "bootloader_components/main/esp32p4_root_journal.c"
    ).read_text()
    traversal = source[
        source.index(
            "specter_boot_journal_state_t specter_root_journal_state("
        ):
    ]
    preamble = r"""
#include <stdint.h>
#include <string.h>
#include "platforms/esp32-p4-wifi6-touch-lcd/common/esp32p4_boot_contract.h"
#include "lib/crc32/crc32.h"
#define SPECTER_JOURNAL_PARTITION_OFFSET 0
#define ESP_OK 0
static uint8_t flash[8192];
static int write_budget = -1;
static int write_calls;
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
static const struct { specter_esp32p4_role_t role; } boot_allowlist[] = {
  {specter_role_boot_a}, {specter_role_boot_b}
};
bool specter_root_journal_append(specter_esp32p4_role_t, uint32_t, specter_boot_journal_state_t);
static int bootloader_flash_write(uint32_t offset, const void *src, size_t n, bool encrypted) {
  (void)encrypted;
  ++write_calls;
  // Synthetic faults: partially written prefix/commit, or fully committed
  // marker with a failed completion report. Never model these as success.
  if ((write_budget == -2 && write_calls == 1) ||
      (write_budget == -3 && write_calls == 2) ||
      (write_budget == -4 && write_calls == 2)) {
    size_t written = write_budget == -4 ? n : (write_calls == 1 ? 16 : 2);
    memcpy(flash + offset, src, written);
    return -1;
  }
  if (write_budget == 0) return -1;
  if (write_budget > 0) --write_budget;
  if (offset + n > sizeof(flash)) return -1;
  memcpy(flash + offset, src, n);
  return 0;
}
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
  return choose_bootloader(s, true);
}
int prepare(const void *data, int trial, int budget, int fallback_approved) {
  memcpy(flash, data, sizeof(flash));
  write_budget = budget;
  write_calls = 0;
  specter_root_state_t s[3] = {0};
  for (int i = 0; i < 2; ++i) {
    s[i].approved = i == trial || fallback_approved;
    s[i].approval.semantic_version = i == trial ? 2 : 1;
    s[i].approval.sequence = i == trial ? 11 : 6;
    s[i].journal = specter_root_journal_state(boot_allowlist[i].role, s[i].approval.sequence);
  }
  return prepare_bootloader(s);
}
void read_flash(void *data) { memcpy(data, flash, sizeof(flash)); }
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
    lib.prepare.argtypes = [ctypes.c_void_p,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.prepare.restype = ctypes.c_int
    lib.read_flash.argtypes = [ctypes.c_void_p]
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


@pytest.mark.parametrize("trial", [0, 1])
@pytest.mark.parametrize("kind", ["stale", "bad_crc", "torn", "confirmed", "attempted", "empty"])
@pytest.mark.parametrize("fallback_approved", [False, True])
def test_prepare_with_real_journal(firmware, trial, kind, fallback_approved):
    stale = journal_record(10)
    bad = bytearray(journal_record(11))
    bad[32] ^= 1
    sectors = {
        "stale": stale * 64,
        "bad_crc": bytes(bad) * 64,
        "torn": (journal_record(11)[:32] + b"\xff" * 32) * 64,
        "confirmed": journal_record(11) * 64,
        "attempted": journal_record(11, 0x4154544D) + b"\xff" * 4032,
        "empty": b"\xff" * 4096,
    }
    fallback = journal_record(6) + b"\xff" * 4032
    raw = sectors[kind] + fallback if trial == 0 else fallback + sectors[kind]
    expected = trial if kind in ("confirmed", "empty") else (
        1 - trial if fallback_approved else -1)
    assert firmware.prepare(raw, trial, -1, fallback_approved) == expected
    after = ctypes.create_string_buffer(8192)
    firmware.read_flash(after)
    fallback_start = (1 - trial) * 4096
    assert after.raw[fallback_start:fallback_start + 4096] == fallback
    if kind != "empty":
        assert after.raw == raw
    regions = [candidate(i + 1, 2 if i == trial else 1, 11 if i == trial else 6,
                         "none") for i in range(2)]
    for i, region in enumerate(regions):
        region["approval"]["state"] = "valid" if i == trial or fallback_approved else "malformed"
        region["journal"] = journal.inspect_sector(AddressSpace([(0, raw)]), i * 4096, i + 1,
                                                   11 if i == trial else 6)
    report = boot_policy.analyze(
        regions, {"checks": {"root_policy": {"status": "pass"}}})
    assert report["selection"] == (
        None if expected < 0 else f"boot_{expected + 1}")


@pytest.mark.parametrize("trial", [0, 1])
@pytest.mark.parametrize("budget", [-4, -3, -2, 0, 1, 2])
@pytest.mark.parametrize("fallback_approved", [False, True])
def test_append_io_failure(firmware, trial, budget, fallback_approved):
    fallback = journal_record(6) + b"\xff" * 4032
    empty = b"\xff" * 4096
    raw = empty + fallback if trial == 0 else fallback + empty
    expected = trial if budget == 2 else (
        1 - trial if fallback_approved else -1)
    assert firmware.prepare(raw, trial, budget, fallback_approved) == expected
    after = ctypes.create_string_buffer(8192)
    firmware.read_flash(after)
    start = (1 - trial) * 4096
    assert after.raw[start:start + 4096] == fallback
    # A failed prefix/commit cannot authorize execution. A later boot uses the
    # durable scan: an uncommitted prefix permits a fresh marker in a new slot.
    state = firmware.traverse(after, trial + 1, 11)
    assert state == (0x4154544D if budget in (-4, 2) else 0)
    assert firmware.prepare(after, trial, -1, fallback_approved) == (
        (1 - trial if fallback_approved else -1) if budget in (-4, 2) else trial
    )


@pytest.mark.parametrize("trial", [0, 1])
def test_full_trial_does_not_try_another_unconfirmed_image(firmware, trial):
    full = journal_record(10) * 64
    empty = b"\xff" * 4096
    raw = full + empty if trial == 0 else empty + full
    assert firmware.prepare(raw, trial, -1, True) == -1
    after = ctypes.create_string_buffer(8192)
    firmware.read_flash(after)
    assert after.raw == raw
    regions = [
        candidate(i + 1, 2 if i == trial else 1, 11 if i == trial else 6,
                  "none", capacity=0 if i == trial else 64)
        for i in range(2)
    ]
    report = boot_policy.analyze(
        regions, {"checks": {"root_policy": {"status": "pass"}}}
    )
    assert report["state"] == "no_loadable_candidate"
    assert report["selection"] is None
