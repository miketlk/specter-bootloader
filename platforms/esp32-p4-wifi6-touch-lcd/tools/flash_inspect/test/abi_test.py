"""C-derived ABI and CRC evidence using the actual production header/library."""

import shutil
import struct
import subprocess
import zlib
import pytest
from flash_inspect import abi
from flash_inspect.backends import ROOT


SOURCE = r"""
#include <stdio.h>
#include <string.h>
#include "platforms/esp32-p4-wifi6-touch-lcd/common/esp32p4_boot_contract.h"
#include "lib/crc32/crc32.h"
int main(void) {
  specter_approval_record_t a = {0};
  memcpy(a.magic, SPECTER_APPROVAL_MAGIC, sizeof(a.magic));
  a.revision = SPECTER_APPROVAL_REVISION;
  a.record_size = sizeof(a);
  memcpy(a.platform, "esp32-p4-wifi6-touch-lcd-5", 25);
  a.role = specter_role_boot_a;
  a.semantic_version = 100000299;
  a.image_length = 512;
  a.sequence = 7;
  a.status = SPECTER_APPROVAL_STATUS_APPROVED;
  a.commit_crc = crc32_fast(&a, offsetof(specter_approval_record_t, commit_crc), 0);
  specter_boot_journal_record_t j = {0};
  j.magic = SPECTER_JOURNAL_MAGIC;
  j.revision = SPECTER_JOURNAL_REVISION;
  j.sequence = 7;
  j.state = specter_journal_confirmed;
  j.commit_crc = crc32_fast(&j, offsetof(specter_boot_journal_record_t, commit_crc), 0);
  uint32_t dimensions[] = {sizeof(a), sizeof(j), offsetof(specter_approval_record_t, commit_crc),
    offsetof(specter_boot_journal_record_t, commit_crc), SPECTER_ESP32P4_TRAILER_SIZE,
    SPECTER_JOURNAL_SECTOR_SIZE, specter_journal_attempted};
  fwrite(dimensions, sizeof(dimensions), 1, stdout);
  fwrite(&a, sizeof(a), 1, stdout);
  fwrite(&j, sizeof(j), 1, stdout);
  return 0;
}
"""


def test_c_contract(tmp_path):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("host C compiler unavailable")
    source = tmp_path / "abi.c"
    source.write_text(SOURCE)
    executable = tmp_path / "abi"
    subprocess.run(
        [
            compiler,
            "-D__BYTE_ORDER=1234",
            "-I",
            str(ROOT),
            str(source),
            str(ROOT / "lib/crc32/crc32.c"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    output = subprocess.check_output([str(executable)])
    assert struct.unpack("<7I", output[:28]) == (
        abi.APPROVAL_SIZE,
        abi.JOURNAL_SIZE,
        abi.APPROVAL_CRC_OFFSET,
        abi.JOURNAL_CRC_OFFSET,
        abi.TRAILER_SIZE,
        abi.JOURNAL_SECTOR_SIZE,
        abi.ATTEMPTED,
    )
    a, j = output[28:156], output[156:]
    assert a[:8] == abi.APPROVAL_MAGIC
    assert struct.unpack_from("<I", a, 8)[0] == abi.APPROVAL_REVISION
    assert struct.unpack_from("<I", a, 72)[0] == abi.APPROVED
    assert struct.unpack_from("<I", a, 112)[0] == zlib.crc32(a[:112])
    assert struct.unpack_from("<I", j, 32)[0] == zlib.crc32(j[:32])
    assert struct.unpack_from("<II", j) == (abi.JOURNAL_MAGIC, abi.JOURNAL_REVISION)
    assert struct.unpack_from("<I", j, 12)[0] == abi.CONFIRMED
