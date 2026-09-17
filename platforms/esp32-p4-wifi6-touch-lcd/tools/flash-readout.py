#!/usr/bin/env python3
"""Resumable ESP32-P4 flash acquisition through the pinned ESP-IDF esptool."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
IDF = ROOT / "third_party/esp-idf"
VERSION = "4.12.0"


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def integer(value):
    try:
        return int(value, 16 if value.lower().startswith("0x") else 10)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "expected decimal or 0x integer") from None


def parser():
    p = Parser(description=__doc__ +
               " JSON stdout; progress stderr. Python 3.10+.")
    p.add_argument("output", type=Path,
                   help="New whole-flash .bin path; never overwritten")
    p.add_argument(
        "--port", help="Explicit serial device (required unless --assemble-only)")
    p.add_argument("--chunks-dir", type=Path, help="Default: OUTPUT.chunks")
    p.add_argument("--chunk-size", type=integer, default=1048576,
                   help="64 KiB to 1 MiB, multiple of 64 KiB (default 1048576)")
    p.add_argument("--baud", type=integer, default=6000000,
                   help="Serial baud, not bytes/s (default 6000000)")
    p.add_argument("--retries", type=int, default=2,
                   help="Retries per operation (default 2)")
    p.add_argument("--timeout", type=int, default=60,
                   help="Seconds per identification/chunk attempt")
    p.add_argument("--verify-timeout", type=int, default=300,
                   help="Seconds per whole-flash verification attempt (default 300)")
    p.add_argument("--resume", action="store_true",
                   help="Reuse hash-checked saved chunks")
    p.add_argument("--assemble-only", action="store_true",
                   help="Offline assembly; no board access")
    p.add_argument("--pretty", action="store_true")
    return p


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_manifest(directory, manifest):
    temporary = directory / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(directory / "manifest.json")


def pinned_backend():
    if Path(os.environ.get("IDF_PATH", "")).resolve() != IDF:
        raise ValueError(
            "source third_party/esp-idf/export.sh before reading hardware")
    env = os.environ.get("IDF_PYTHON_ENV_PATH")
    if not env:
        raise ValueError(
            "missing IDF_PYTHON_ENV_PATH; source pinned export.sh")
    python = Path(env) / "bin/python"

    def git(*args):
        return subprocess.check_output(["git", *args], text=True, timeout=10).strip()
    revision = git("-C", str(IDF), "rev-parse", "HEAD")
    if revision != git("-C", str(ROOT), "rev-parse", "HEAD:third_party/esp-idf"):
        raise ValueError("ESP-IDF checkout does not match repository gitlink")
    version = subprocess.check_output(
        [str(python), "-I", "-m", "esptool", "version"], text=True, timeout=10
    ).strip().splitlines()
    if not version or version[-1] != VERSION:
        raise ValueError(
            f"requires qualified pinned-environment esptool {VERSION}")
    return [str(python), "-I", "-m", "esptool"], revision


class Reader:
    def __init__(self, args, directory):
        self.args = args
        self.directory = directory
        self.command, self.revision = pinned_backend()

    def run(self, label, operation):
        for attempt in range(self.args.retries + 1):
            chunk_path = Path(
                operation[3]) if operation[0] == "read_flash" else None
            if chunk_path:
                chunk_path.unlink(missing_ok=True)
            command = self.command + [
                "--chip", "esp32p4", "--port", self.args.port,
                "--baud", str(self.args.baud), "--after", "no_reset",
            ] + operation
            start = time.monotonic()
            timeout = (self.args.verify_timeout if operation[0] == "verify_flash"
                       else self.args.timeout)
            try:
                result = subprocess.run(command, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=timeout)
                log = result.stdout.decode("utf-8", errors="replace")
                success = result.returncode == 0
                if success and chunk_path:
                    success = (chunk_path.is_file()
                               and chunk_path.stat().st_size == int(operation[2]))
                    if not success:
                        log += "\nMissing or short chunk output\n"
            except subprocess.TimeoutExpired as error:
                log = (error.stdout or b"").decode(
                    "utf-8", errors="replace") + "\nTIMEOUT\n"
                success = False
            with (self.directory / "esptool.log").open("a") as stream:
                stream.write(f"\n{label} attempt={attempt + 1} baud={self.args.baud} "
                             f"seconds={time.monotonic() - start:.3f}\n{log}")
            if success:
                return log
            print(
                f"{label}: attempt {attempt + 1} failed; see esptool.log", file=sys.stderr)
        raise ValueError(f"{label} failed after {self.args.retries + 1} attempts; "
                         f"see {self.directory / 'esptool.log'}; rerun with --resume")

    def identify(self):
        log = self.run("identify", ["flash_id"])
        mac = re.search(r"MAC: ([0-9a-f:]{17})", log, re.I)
        size = re.search(r"Detected flash size: (\d+)MB", log)
        jedec = re.search(r"Manufacturer: (\w+)\s+Device: (\w+)", log)
        if not mac or not size or not jedec:
            raise ValueError(
                "could not determine ESP32-P4 identity and flash capacity")
        capacity = int(size[1]) * 1048576
        if not 65536 <= capacity <= 64 * 1048576:
            raise ValueError("unsupported flash capacity")
        return {"mac": mac[1].lower(), "flash_size": capacity,
                "manufacturer": jedec[1], "device": jedec[2]}


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("unsupported chunk manifest")
    if not isinstance(manifest.get("device"), dict):
        raise ValueError("invalid device identity")
    size, chunk = manifest["device"]["flash_size"], manifest["chunk_size"]
    if (type(size) is not int or not 65536 <= size <= 64 * 1048576
            or type(chunk) is not int or not 65536 <= chunk <= 1048576
            or chunk % 65536 or size % 65536):
        raise ValueError("invalid manifest geometry")
    if not isinstance(manifest["chunks"], dict):
        raise ValueError("invalid chunk index")
    if any(not isinstance(record, dict) for record in manifest["chunks"].values()):
        raise ValueError("invalid chunk record")
    return size, chunk


def chunk_data(directory, manifest, offset, length):
    name = f"{offset:08x}.bin"
    record = manifest["chunks"].get(name)
    if record is None:
        return None
    path = directory / name
    if not path.is_file():
        return None
    data = path.read_bytes()
    if len(data) != length or digest(data) != record.get("sha256"):
        return None
    return data


def assemble(directory, manifest, output, reader=None):
    size, chunk = validate_manifest(manifest)
    temporary = output.with_name(output.name + ".partial")
    sha = hashlib.sha256()
    stream = temporary.open("xb")
    try:
        with stream:
            for offset in range(0, size, chunk):
                data = chunk_data(directory, manifest, offset,
                                  min(chunk, size - offset))
                if data is None:
                    raise ValueError(
                        f"missing or corrupt chunk at 0x{offset:08x}")
                stream.write(data)
                sha.update(data)
            stream.flush()
            os.fsync(stream.fileno())
        if reader:
            reader.run("verify-whole-flash",
                       ["verify_flash", "0", str(temporary)])
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"output": str(output), "size": size, "sha256": sha.hexdigest(),
            "format": "bin", "base_offset": 0,
            "device_verified": reader is not None}


def execute(args):
    if not 65536 <= args.chunk_size <= 1048576 or args.chunk_size % 65536:
        raise ValueError(
            "--chunk-size must be a multiple of 64 KiB between 64 KiB and 1 MiB")
    if (not 1 <= args.baud <= 6000000 or not 0 <= args.retries <= 20
            or args.timeout <= 0 or args.verify_timeout <= 0):
        raise ValueError("invalid baud, retries (0..20), or timeout")
    if not args.assemble_only and not args.port:
        raise ValueError("--port is required for hardware reads")
    output = args.output.absolute()
    if output.exists() or output.with_name(output.name + ".partial").exists():
        raise ValueError(
            "output or .partial already exists; choose a new output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    directory = (args.chunks_dir or Path(str(output) + ".chunks")).absolute()
    directory.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("chunk directory is in use") from None
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text()) if path.exists() else None
        if args.assemble_only:
            if manifest is None:
                raise ValueError("--assemble-only requires a saved manifest")
            result = assemble(directory, manifest, output)
        else:
            if manifest is not None and not args.resume:
                raise ValueError(
                    "chunk manifest exists; use --resume or a new --chunks-dir")
            reader = Reader(args, directory)
            device = reader.identify()
            if manifest is not None:
                validate_manifest(manifest)
                if manifest["device"] != device or manifest["idf_revision"] != reader.revision:
                    raise ValueError(
                        "resume device or ESP-IDF revision mismatch")
                if manifest["chunk_size"] != args.chunk_size:
                    raise ValueError(
                        "resume --chunk-size must match saved manifest")
            else:
                manifest = {"schema_version": 1, "device": device,
                            "idf_revision": reader.revision, "esptool_version": VERSION,
                            "chunk_size": args.chunk_size, "chunks": {}}
                save_manifest(directory, manifest)
            size = device["flash_size"]
            for offset in range(0, size, args.chunk_size):
                length = min(args.chunk_size, size - offset)
                name = f"{offset:08x}.bin"
                if chunk_data(directory, manifest, offset, length) is not None:
                    print(f"reuse {name}", file=sys.stderr)
                    continue
                temporary = directory / (name + ".partial")
                temporary.unlink(missing_ok=True)
                reader.run(name, ["read_flash", str(offset), str(length),
                                  str(temporary), "--no-progress"])
                data = temporary.read_bytes()
                if len(data) != length:
                    raise ValueError(
                        f"short chunk {name}; rerun with --resume")
                temporary.replace(directory / name)
                manifest["chunks"][name] = {"sha256": digest(data), "size": length,
                                            "baud": args.baud}
                save_manifest(directory, manifest)
                print(f"read {offset + length}/{size} bytes", file=sys.stderr)
            result = assemble(directory, manifest, output, reader)
        return dict(result, chunks_dir=str(directory), device=manifest["device"])


def main(argv=None):
    args = None
    try:
        args = parser().parse_args(argv)
        result = dict(execute(args), schema_version=1, status="ok")
        code = 0
    except KeyboardInterrupt:
        result, code = {"schema_version": 1, "status": "interrupted",
                        "error": "saved chunks retained; rerun with --resume"}, 130
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result, code = {"schema_version": 1,
                        "status": "error", "error": str(error)}, 2
    print(json.dumps(result, indent=2 if args and args.pretty else None, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
