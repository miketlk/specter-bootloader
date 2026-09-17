"""JSON-only error boundary and offline inspection CLI."""

import argparse
import json
from pathlib import Path
from . import __version__


class UsageError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def integer(value):
    try:
        result = int(value, 16 if value.lower().startswith("0x") else 10)
        if result < 0:
            raise ValueError()
        return result
    except ValueError:
        raise argparse.ArgumentTypeError(
            "expected a nonnegative decimal or 0x integer"
        ) from None


def parser():
    p = Parser(
        description="Offline ESP32-P4 flash inspection; Python 3.10+. JSON on stdout (except help/version)."
    )
    p.add_argument("input", type=Path)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--format",
        choices=("auto", "bin", "intelhex", "hex", "xxd", "hexdump"),
        default="auto",
        help="Auto recognizes text grammar; ASCII hex bytes are ambiguous: use --format bin to preserve them.",
    )
    p.add_argument(
        "--base-offset", type=integer, help="Binary/plain hex base only (default 0)."
    )
    p.add_argument("--partition-table-offset", type=integer)
    p.add_argument("--expected-layout", type=Path)
    p.add_argument("--board", choices=("auto", "lcd-4p3", "lcd-5"), default="auto")
    p.add_argument(
        "--content-mode", choices=("auto", "plaintext", "ciphertext"), default="auto"
    )
    p.add_argument(
        "--max-span",
        type=integer,
        default=64 * 1024 * 1024,
        help="Maximum represented address span in bytes (default 64 MiB).",
    )
    p.add_argument(
        "--max-source-size",
        type=integer,
        default=512 * 1024 * 1024,
        help="Maximum input file size in bytes (default 512 MiB).",
    )
    p.add_argument(
        "--max-input-ranges",
        type=integer,
        default=262144,
        help="Maximum text record count (default 262144).",
    )
    p.add_argument("--pretty", action="store_true")
    p.add_argument("--strict", action="store_true")
    return p


def main(argv=None):
    from .report import empty_report, finding, inspect
    from . import input_formats, layout
    from .backends import PLATFORM

    result = empty_report()
    args = None
    exit_code = 2
    stage = "INVOCATION_INVALID"
    try:
        args = parser().parse_args(argv)
        stage = "CONFIGURATION_INVALID"
        default_offset, _ = layout.defaults()
        if args.partition_table_offset is None:
            args.partition_table_offset = default_offset
        if (
            not 0x3000 <= args.partition_table_offset <= 2**32 - 4096
            or args.partition_table_offset % 4096
        ):
            raise ValueError(
                "partition table offset must be aligned and after Root start"
            )
        expected, digest = layout.expected(
            args.expected_layout or PLATFORM / "partitions.csv",
            args.partition_table_offset,
        )
        stage = "INPUT_FORMAT_INVALID"
        with args.input.open("rb") as stream:
            data = stream.read(args.max_source_size + 1)
        if len(data) > args.max_source_size:
            raise ValueError("source exceeds --max-source-size")
        space, fmt, metadata = input_formats.decode(
            data, args.format, args.base_offset, args.max_span, args.max_input_ranges
        )
        result = inspect(space, fmt, data, metadata, args, expected, digest)
        exit_code = (
            1 if args.strict and result["summary"]["validation"] != "pass" else 0
        )
    except (UsageError, ValueError, OSError) as error:
        finding(result, stage, details={"reason": str(error)[:512]})
        result["status"] = "error"
    except ImportError as error:
        finding(result, "DEPENDENCY_UNAVAILABLE", details={"reason": str(error)[:512]})
        result["status"] = "error"
    except Exception:
        finding(
            result,
            "INTERNAL_ERROR",
            details={"reason": "Unexpected internal inspection failure"},
        )
        result["status"] = "error"
        exit_code = 3
    result["summary"]["finding_count"] = len(result["findings"])
    print(
        json.dumps(
            result,
            indent=2 if args and args.pretty else None,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return exit_code
