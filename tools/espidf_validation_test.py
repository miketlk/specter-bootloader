"""Address-policy regressions through both ESP32 packaging entry points."""

import importlib.util
import io
import os
from pathlib import Path
import re

import click
import pytest

from core.espidf import ESP32P4_REGIONS
from esp32_image_test_support import minimal_esp32p4_app_image


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["upgrade-generator", "make-initial-firmware"])
def package_image(request):
    if (importlib.util.find_spec("esptool") is None
            and "IDF_PYTHON_ENV_PATH" not in os.environ):
        pytest.skip("source third_party/esp-idf/export.sh for image validation")
    path = Path(__file__).with_name(request.param + ".py")
    spec = importlib.util.spec_from_file_location(request.param, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def package(image):
        platform = "esp32-p4-wifi6-touch-lcd-4p3"
        if request.param == "upgrade-generator":
            source = io.BytesIO(image)
            source.name = "fixture.bin"
            return module.create_esp_idf_payload_section(source, "main", platform)
        return module.make_esp32p4_trailer(image, platform, "main", 8)

    return package


def test_policy_bounds_match_pinned_idf():
    """Fail visibly if IDF's SoC address definitions drift from host policy."""
    soc_dir = REPO_ROOT / "third_party/esp-idf/components/soc/esp32p4/include/soc"
    header = (soc_dir / "soc.h").read_text()
    definitions = dict(re.findall(
        r"^#define\s+(SOC_\w+)\s+(0x[0-9a-fA-F]+)\b", header, re.MULTILINE
    ))
    for region, aliases in {
        "flash": ["IROM", "DROM"],
        "psram": ["EXTRAM"],
        "ram": ["IRAM", "DRAM"],
        "rtc": ["RTC_IRAM", "RTC_DRAM"],
        "spm": ["SPM"],
    }.items():
        for alias in aliases:
            assert ESP32P4_REGIONS[region] == tuple(
                int(definitions[f"SOC_{alias}_{bound}"], 16)
                for bound in ["LOW", "HIGH"]
            )
    caps = (soc_dir / "soc_caps.h").read_text()
    for capability in [
        "SOC_MMU_PER_EXT_MEM_TARGET", "SOC_RTC_FAST_MEM_SUPPORTED",
        "SOC_MEM_SPM_SUPPORTED",
    ]:
        assert re.search(
            rf"^#define\s+{capability}\s+\(?1\)?\s", caps, re.MULTILINE)


@pytest.mark.parametrize("address", [
    0x44000020, 0x47FF0020, 0x4FC00020, 0x50100020, 0x600FE020,
    0x4FF00020, 0x20,
])
def test_rejects_descriptor_outside_mapped_regions(package_image, address):
    image = minimal_esp32p4_app_image(app_address=address)
    with pytest.raises(click.ClickException, match="application descriptor"):
        package_image(image)


@pytest.mark.parametrize("address", [
    0x44000154, 0x47FF0154, 0x4FC00000, 0x50100000, 0x600FE000,
    0x43FFFFFC, 0x4BFFFFFC, 0x4FFBFFFC, 0x5010FFFC, 0x30101FFC,
    0x0FFFFFFC, 0xFFFFFFFC,
])
def test_rejects_invalid_or_crossing_segment(package_image, address):
    image = minimal_esp32p4_app_image(extra_segments=[(address, bytes(8))])
    with pytest.raises(click.ClickException, match="segment load range"):
        package_image(image)


@pytest.mark.parametrize("address", [0x40000158, 0x48000158])
def test_rejects_misaligned_mapping(package_image, address):
    # The second segment's data starts at file offset 0x154.
    image = minimal_esp32p4_app_image(extra_segments=[(address, bytes(8))])
    with pytest.raises(click.ClickException, match="misaligned"):
        package_image(image)


@pytest.mark.parametrize("entrypoint", [0, 0x30100000, 0x4FF00008])
def test_rejects_entrypoint_in_padding_data_or_outside_segments(package_image, entrypoint):
    image = minimal_esp32p4_app_image(
        extra_segments=[(0, bytes(8)), (0x30100000, bytes(8)),
                        (0x4FF00000, bytes(8))],
        entrypoint=entrypoint,
    )
    with pytest.raises(click.ClickException, match="executable image segments"):
        package_image(image)


@pytest.mark.parametrize("app_address", [0x40000020, 0x48000020])
def test_accepts_both_descriptor_mappings(package_image, app_address):
    assert package_image(minimal_esp32p4_app_image(app_address=app_address))


@pytest.mark.parametrize("address", [0x43FF0154, 0x4BFF0154])
def test_accepts_mapping_ending_at_region_boundary(package_image, address):
    image = minimal_esp32p4_app_image(
        extra_segments=[(address, bytes(0x10000 - 0x154))],
        entrypoint=address,
    )
    assert package_image(image)


@pytest.mark.parametrize("address", [
    0x40000154, 0x48000154, 0x4FF00000, 0x4FFBFFFC, 0x5010FFFC,
])
def test_accepts_executable_segments_including_last_ram_word(package_image, address):
    image = minimal_esp32p4_app_image(
        extra_segments=[(address, bytes(4))], entrypoint=address,
    )
    assert package_image(image)


def test_accepts_padding_and_spm_data(package_image):
    image = minimal_esp32p4_app_image(
        extra_segments=[(0, bytes(8)), (0x30101FFC, bytes(4))],
    )
    assert package_image(image)


@pytest.mark.parametrize("directory,filename", [
    ("plaintext-dev-boot-a", "specter_esp32p4_bootloader.bin"),
    ("mock-plaintext-dev-4p3-main-bloat-0", "specter_esp32p4_mock_main.bin"),
    ("mock-plaintext-dev-5-main-bloat-0", "specter_esp32p4_mock_main.bin"),
])
def test_accepts_local_idf_builds(package_image, directory, filename):
    path = REPO_ROOT / "build/esp32-p4-wifi6-touch-lcd" / directory / filename
    if not path.is_file():
        pytest.skip(f"optional ESP-IDF build fixture is unavailable: {path}")
    assert package_image(path.read_bytes())
