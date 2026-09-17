"""Explicit checkout-relative backend loading, with no ambient IDF environment."""

import contextlib
import importlib.util
import io
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2]
ROOT = PLATFORM.parents[1]
IDF = ROOT / "third_party/esp-idf"


def load(name, path):
    if not path.is_file():
        raise ImportError(f"required backend missing: {path.name}")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        spec.loader.exec_module(module)
    return module


def partition_backend(offset):
    module = load(
        "flash_inspect_idf_partition",
        IDF / "components/partition_table/gen_esp32part.py",
    )
    module.offset_part_table = offset
    return module


def nvs_backend():
    return load(
        "flash_inspect_idf_nvs",
        IDF / "components/nvs_flash/nvs_partition_tool/nvs_parser.py",
    )


def app_validator():
    # Call the direct API: never fall back to an ambient IDF virtualenv.
    import esptool  # noqa: F401

    return load(
        "flash_inspect_app_validator", ROOT / "tools/core/espidf.py"
    )._validate_with_esptool
