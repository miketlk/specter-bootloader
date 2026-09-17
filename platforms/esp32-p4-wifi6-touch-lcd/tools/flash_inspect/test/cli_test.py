import json
import subprocess
import sys
import tempfile
import pytest
from jsonschema import Draft202012Validator
from flash_inspect.backends import PLATFORM
from fixtures import snapshot

LAUNCHER = PLATFORM / "tools/flash-inspect.py"
SCHEMA = json.loads(
    (PLATFORM / "tools/flash_inspect/schema/report-v1.schema.json").read_text()
)


def run(*args):
    with tempfile.TemporaryDirectory() as directory:
        proc = subprocess.run(
            [sys.executable, str(LAUNCHER), *map(str, args)],
            cwd=directory,
            capture_output=True,
            timeout=30,
        )
    report = json.loads(proc.stdout)
    Draft202012Validator(SCHEMA).validate(report)
    assert not proc.stderr
    return proc, report


def test_healthy_strict_and_deterministic(tmp_path):
    path = tmp_path / "dump.bin"
    data = snapshot()
    path.write_bytes(data)
    proc, r = run(path, "--strict")
    assert proc.returncode == 0, r["findings"]
    assert r["status"] == "ok"
    assert r["summary"]["validation"] == "pass"
    assert r["boot_analysis"]["selection"] == "partition:2:boot_a"
    assert r["expectations"]["inferred_board"] == "lcd-5"
    assert run(path, "--strict")[0].stdout == proc.stdout
    assert path.read_bytes() == data


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--bad"],
        ["/no/such/file"],
        ["/dev/null", "--base-offset", "-1"],
        ["/dev/null", "--partition-table-offset", "4"],
    ],
)
def test_errors(args):
    proc, r = run(*args)
    assert proc.returncode == 2
    assert r["status"] == "error"


def test_partial_and_default_findings(tmp_path):
    path = tmp_path / "dump.bin"
    path.write_bytes(b"\xff" * 100)
    assert run(path)[0].returncode == 0
    assert run(path, "--strict")[0].returncode == 1
    path.write_bytes(bytes(100))
    assert run(path, "--content-mode", "plaintext")[0].returncode == 0


def test_malformed_text_and_limits(tmp_path):
    path = tmp_path / "bad.bin"
    path.write_bytes(b":abcdef")
    proc, r = run(path)
    assert proc.returncode == 2 and r["findings"][0]["code"] == "INPUT_FORMAT_INVALID"
    assert run(path, "--max-source-size", "1")[0].returncode == 2


def test_ciphertext_journal_recovery(tmp_path):
    path = tmp_path / "cipher.bin"
    data = snapshot()
    data[0x10000:0x11000] = bytes(4096)
    path.write_bytes(data)
    _, r = run(path, "--content-mode", "ciphertext")
    assert r["boot_analysis"]["selection"] is None
    recovery = next(r for r in r["regions"] if r["id"] == "recovery:boot_journal")
    assert recovery["journal"]["sectors"][0]["records"][0]["state_name"] == "confirmed"
    assert not any(f["code"] == "IMAGE_HASH_MISMATCH" for f in r["findings"])


def test_help_without_site_packages():
    proc = subprocess.run(
        [sys.executable, "-S", str(LAUNCHER), "--help"], capture_output=True
    )
    assert proc.returncode == 0 and b"Offline" in proc.stdout


def test_missing_esptool_json(tmp_path):
    path = tmp_path / "dump.bin"
    path.write_bytes(snapshot())
    proc = subprocess.run(
        [sys.executable, "-S", str(LAUNCHER), str(path)], capture_output=True
    )
    r = json.loads(proc.stdout)
    Draft202012Validator(SCHEMA).validate(r)
    assert proc.returncode == 2 and r["findings"][0]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_configuration_error(tmp_path):
    path = tmp_path / "layout.csv"
    path.write_text("not,a,valid,partition,row\n")
    proc, report = run("/dev/null", "--expected-layout", path)
    assert proc.returncode == 2
    assert report["findings"][0]["code"] == "CONFIGURATION_INVALID"


def test_sanitized_internal_error(monkeypatch, capsys):
    from flash_inspect import cli, layout

    def fail():
        raise RuntimeError("private implementation detail")

    monkeypatch.setattr(layout, "defaults", fail)
    assert cli.main(["/dev/null"]) == 3
    output = capsys.readouterr()
    report = json.loads(output.out)
    Draft202012Validator(SCHEMA).validate(report)
    assert "private implementation detail" not in output.out
    assert report["findings"][0]["code"] == "INTERNAL_ERROR"


def test_schema_rejects_unknown_as_false(tmp_path):
    from jsonschema import ValidationError

    path = tmp_path / "empty"
    path.write_bytes(b"")
    _, report = run(path)
    report["regions"][0]["coverage"]["status"] = False
    with pytest.raises(ValidationError):
        Draft202012Validator(SCHEMA).validate(report)


def test_cli_uses_portable_foreign_working_directory(tmp_path, monkeypatch):
    from pathlib import Path

    # Simulate a host where only its configured temporary directory is usable.
    # The real subprocess still runs, exercising checkout-relative backends.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    real_run = subprocess.run
    working_directories = []

    def run_in_portable_directory(*args, **kwargs):
        directory = Path(kwargs["cwd"])
        assert directory.parent == tmp_path
        assert directory.is_dir()
        working_directories.append(directory)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run_in_portable_directory)
    path = tmp_path / "dump.bin"
    path.write_bytes(snapshot())
    proc, report = run(path, "--strict")
    assert proc.returncode == 0
    assert report["summary"]["validation"] == "pass"
    assert working_directories
