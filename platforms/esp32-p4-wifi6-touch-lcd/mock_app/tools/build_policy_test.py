"""Safety policy regressions for the ESP32-P4 build wrapper."""

import os
import subprocess
from pathlib import Path


BUILD = Path(__file__).resolve().parents[2] / "tools" / "build.sh"


def run_build(*arguments: str, **environment: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(environment)
    return subprocess.run(
        [BUILD, *arguments], capture_output=True, text=True, env=env, check=False
    )


def test_mock_rejects_production_profile_before_building():
    result = run_build(
        "encrypted-production", "main", "build", SPECTER_APP="mock-main"
    )
    assert result.returncode == 2
    assert "mock-main is allowed only for plaintext-dev:main" in result.stderr


def test_production_flash_requires_separate_hardware_opt_in():
    result = run_build("encrypted-production", "boot-a", "app-flash")
    assert result.returncode == 2
    assert "SPECTER_ALLOW_IRREVERSIBLE_HARDWARE=1" in result.stderr
