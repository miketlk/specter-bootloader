"""Validation of canonical ESP32-P4 application images via ESP-IDF esptool."""

import io
import os
from pathlib import Path
import subprocess
import sys


ESP32P4_CHIP_ID = 18
ESP_APP_DESC_SIZE = 256
ESP_APP_DESC_MMU_PAGE_SIZE_OFFSET = 180
# Address policy from the pinned ESP-IDF checkout:
# components/soc/esp32p4/include/soc/soc.h and
# components/bootloader_support/src/esp_image_format.c (should_map,
# should_load, verify_load_addresses). Ends are exclusive. Esptool's display
# memory map includes ROM and merges the flash/PSRAM gap, so cannot authorize
# segment destinations. Tests check these bounds against the pinned headers.
ESP32P4_REGIONS = {
    "flash": (0x40000000, 0x44000000),
    "psram": (0x48000000, 0x4C000000),
    "ram": (0x4FF00000, 0x4FFC0000),
    "rtc": (0x50108000, 0x50110000),
    "spm": (0x30100000, 0x30102000),
}
ESP32P4_MAPPED_REGIONS = {"flash", "psram"}
# esp_hw_support/esp_memory_utils.c: esp_ptr_executable(). ROM cannot be
# loaded by an app; SPM is loadable data but is not an accepted HP entry point.
ESP32P4_EXECUTABLE_REGIONS = {"flash", "psram", "ram", "rtc"}


class EspIdfImageError(ValueError):
    """Raised when an input is not a canonical ESP32-P4 app image."""


def _segment_region(segment):
    """Classify a complete segment, excluding gaps, ROM and MMIO."""
    end = segment.addr + len(segment.data)
    if segment.addr < 0 or end > 0x100000000:
        return None
    # should_load() reserves low addresses for non-loaded segments (padding).
    if segment.addr < 0x10000000 and end <= 0x10000000:
        return "padding"
    for region, (start, stop) in ESP32P4_REGIONS.items():
        if start <= segment.addr < stop and end <= stop:
            return region
    return None


def _validate_with_esptool(payload):
    from esptool.bin_image import LoadFirmwareImage
    from esptool.cmds import _parse_app_info

    try:
        image = LoadFirmwareImage("esp32p4", io.BytesIO(payload))
        if image.chip_id != ESP32P4_CHIP_ID:
            raise EspIdfImageError("image targets a different ESP chip")
        if not image.segments:
            raise EspIdfImageError("image has no segments")
        if not image.append_digest or len(image.stored_digest) != 32:
            raise EspIdfImageError("image has no appended SHA-256")
        if image.checksum != image.calculate_checksum():
            raise EspIdfImageError("image checksum is invalid")
        if image.stored_digest != image.calc_digest:
            raise EspIdfImageError("appended SHA-256 is invalid")
        if image.data_length + 32 != len(payload):
            raise EspIdfImageError("image has trailing or missing bytes")

        app_segment = image.segments[0]
        if (
            len(app_segment.data) < ESP_APP_DESC_SIZE
            or _segment_region(app_segment) not in ESP32P4_MAPPED_REGIONS
            or _parse_app_info(app_segment.data) is None
        ):
            raise EspIdfImageError("image has no valid application descriptor")
        mmu_page_log2 = app_segment.data[ESP_APP_DESC_MMU_PAGE_SIZE_OFFSET]
        mmu_page_size = 1 << mmu_page_log2 if mmu_page_log2 else image.IROM_ALIGN
        if mmu_page_size < 0x1000 or mmu_page_size > image.IROM_ALIGN:
            raise EspIdfImageError(
                "application descriptor has invalid MMU page size")

        entry_point_valid = False
        for segment in image.segments:
            region = _segment_region(segment)
            if len(segment.data) % 4 or region is None:
                raise EspIdfImageError(
                    "image has an invalid segment load range")
            if region in ESP32P4_MAPPED_REGIONS:
                data_offset = segment.file_offs + 8
                if data_offset % mmu_page_size != segment.addr % mmu_page_size:
                    raise EspIdfImageError(
                        "flash-mapped segment is misaligned")
            if (
                region in ESP32P4_EXECUTABLE_REGIONS
                and segment.addr <= image.entrypoint < segment.addr + len(segment.data)
            ):
                entry_point_valid = True
        if not entry_point_valid:
            raise EspIdfImageError(
                "entry point is outside executable image segments")
    except EspIdfImageError:
        raise
    except Exception as error:
        raise EspIdfImageError(
            "malformed ESP32-P4 application image") from error
    return bytes(image.stored_digest)


def _validate_with_idf_python(payload):
    idf_python_env = os.environ.get("IDF_PYTHON_ENV_PATH")
    if not idf_python_env:
        raise EspIdfImageError(
            "ESP-IDF esptool is unavailable; source third_party/esp-idf/export.sh"
        )
    idf_python = Path(idf_python_env) / "bin" / "python"
    if not idf_python.is_file():
        raise EspIdfImageError("ESP-IDF Python environment is invalid")
    result = subprocess.run(
        [str(idf_python), str(Path(__file__).resolve()), "--validate-stdin"],
        input=bytes(payload),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise EspIdfImageError(
            message or "malformed ESP32-P4 application image")
    try:
        return bytes.fromhex(result.stdout.decode("ascii").strip())
    except ValueError as error:
        raise EspIdfImageError(
            "ESP-IDF validator returned an invalid digest"
        ) from error


def validate_esp32p4_app_image(payload):
    """Validate and return the appended SHA-256 of an ESP32-P4 app image.

    ESP-IDF's pinned esptool parser remains authoritative for image decoding.
    The checks here enforce the canonical subset required by the on-device
    ``esp_image_verify()`` path and reject bytes after the appended digest.
    Root Loader still checks runtime restrictions such as overlap with its
    own stack and linked sections; host validation does not prove executability.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise EspIdfImageError("image must be bytes-like")
    try:
        import esptool  # noqa: F401
    except ImportError:
        return _validate_with_idf_python(payload)
    return _validate_with_esptool(payload)


def _main():
    if sys.argv[1:] != ["--validate-stdin"]:
        return 2
    try:
        digest = _validate_with_esptool(sys.stdin.buffer.read())
    except (EspIdfImageError, ImportError) as error:
        print(error, file=sys.stderr)
        return 1
    print(digest.hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
