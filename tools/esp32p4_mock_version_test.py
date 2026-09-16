"""Tests for mock semantic-version to tag10 conversion."""

from pathlib import Path
import subprocess

import pytest


_ENCODER = (
    Path(__file__).parents[1]
    / "platforms"
    / "esp32-p4-wifi6-touch-lcd"
    / "mock_app"
    / "mock_version.cmake"
)
_TEST_SCRIPT = Path(__file__).with_name("mock_version_test.cmake")


@pytest.mark.parametrize(
    "version,tag",
    [
        ("1.2.3", "0100200399"),
        ("1.0.999", "0100099999"),
        ("41.999.999", "4199999999"),
    ],
)
def test_mock_version_tag_matches_project_version(version, tag):
    subprocess.run(
        [
            "cmake",
            f"-DENCODER={_ENCODER}",
            f"-DVERSION={version}",
            f"-DEXPECTED={tag}",
            "-P",
            str(_TEST_SCRIPT),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("version", ["42.0.0", "1.1000.0", "1.0.1000"])
def test_mock_version_rejects_values_above_bl_version_max(version):
    result = subprocess.run(
        [
            "cmake",
            f"-DENCODER={_ENCODER}",
            f"-DVERSION={version}",
            "-DEXPECTED=unused",
            "-P",
            str(_TEST_SCRIPT),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
