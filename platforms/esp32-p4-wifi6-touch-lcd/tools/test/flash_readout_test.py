"""Acquisition failures must not publish partial or mixed flash images."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    "flash_readout", Path(__file__).resolve().parents[1] / "flash-readout.py"
)
readout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readout)


def fixture_chunks(tmp_path):
    directory = tmp_path / "chunks"
    directory.mkdir()
    data = bytes(range(256)) * 256 + b"\xff" * 65536
    manifest = {"schema_version": 1, "device": {"flash_size": len(data)},
                "chunk_size": 65536, "chunks": {}}
    for offset in (0, 65536):
        name = f"{offset:08x}.bin"
        chunk = data[offset:offset + 65536]
        (directory / name).write_bytes(chunk)
        manifest["chunks"][name] = {"sha256": readout.digest(chunk)}
    readout.save_manifest(directory, manifest)
    return directory, manifest, data


def test_offline_assembly_and_json(tmp_path, capsys):
    directory, _, data = fixture_chunks(tmp_path)
    output = tmp_path / "flash.bin"
    assert readout.main([str(output), "--assemble-only",
                         "--chunks-dir", str(directory)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert output.read_bytes() == data
    assert result["sha256"] == readout.digest(data)
    assert result["base_offset"] == 0
    assert result["device_verified"] is False


@pytest.mark.parametrize("damage", ["missing", "short", "corrupt"])
def test_bad_chunk_never_publishes(tmp_path, damage):
    directory, manifest, _ = fixture_chunks(tmp_path)
    chunk = directory / "00010000.bin"
    if damage == "missing":
        chunk.unlink()
    else:
        chunk.write_bytes(b"x" * (42 if damage == "short" else 65536))
    output = tmp_path / "flash.bin"
    with pytest.raises(ValueError, match="missing or corrupt"):
        readout.assemble(directory, manifest, output)
    assert not output.exists()
    assert not output.with_suffix(".bin.partial").exists()


def test_existing_image_preserved(tmp_path):
    directory, manifest, _ = fixture_chunks(tmp_path)
    output = tmp_path / "flash.bin"
    output.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        readout.assemble(directory, manifest, output)
    assert output.read_bytes() == b"original"


def test_existing_partial_preserved(tmp_path):
    directory, manifest, _ = fixture_chunks(tmp_path)
    output = tmp_path / "flash.bin"
    partial = output.with_suffix(".bin.partial")
    partial.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        readout.assemble(directory, manifest, output)
    assert partial.read_bytes() == b"original"


def test_device_verification_failure_never_publishes(tmp_path):
    directory, manifest, _ = fixture_chunks(tmp_path)

    class ChangedDevice:
        def run(self, label, operation):
            assert operation[0] == "verify_flash"
            raise ValueError("digest mismatch")
    output = tmp_path / "flash.bin"
    with pytest.raises(ValueError, match="digest mismatch"):
        readout.assemble(directory, manifest, output, ChangedDevice())
    assert not output.exists()


def test_retry_timeout_then_success(tmp_path, monkeypatch):
    monkeypatch.setattr(readout, "pinned_backend", lambda: (
        ["python", "-m", "esptool"], "pin"))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, 1, output=b"partial log")
        return subprocess.CompletedProcess(command, 0, b"success")
    monkeypatch.setattr(readout.subprocess, "run", run)
    args = readout.parser().parse_args(
        [str(tmp_path / "out.bin"), "--port", "test"])
    reader = readout.Reader(args, tmp_path)
    assert reader.run("identify", ["flash_id"]) == "success"
    assert len(calls) == 2
    assert "TIMEOUT" in (tmp_path / "esptool.log").read_text()


def test_retry_exhaustion(tmp_path, monkeypatch):
    monkeypatch.setattr(readout, "pinned_backend", lambda: (["python"], "pin"))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 2, b"failure")
    monkeypatch.setattr(readout.subprocess, "run", run)
    args = readout.parser().parse_args(
        ["out.bin", "--port", "test", "--retries", "1"])
    with pytest.raises(ValueError, match="after 2 attempts"):
        readout.Reader(args, tmp_path).run("identify", ["flash_id"])
    assert len(calls) == 2


@pytest.mark.parametrize("extra", [[], ["--chunk-size", "65535"], ["--baud", "0"],
                                   ["--retries", "-1"], ["--unknown"]])
def test_invalid_cli_is_json(extra, capsys):
    assert readout.main(["out.bin", *extra]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_resume_reuses_good_and_rereads_corrupt_chunks(tmp_path, monkeypatch):
    directory, manifest, data = fixture_chunks(tmp_path)
    manifest["idf_revision"] = "pin"
    readout.save_manifest(directory, manifest)
    (directory / "00010000.bin").write_bytes(b"bad")
    calls = []

    class FakeReader:
        revision = "pin"

        def __init__(self, args, directory):
            pass

        def identify(self):
            return manifest["device"]

        def run(self, label, operation):
            calls.append(operation)
            if operation[0] == "read_flash":
                start, size = int(operation[1]), int(operation[2])
                Path(operation[3]).write_bytes(data[start:start + size])
    monkeypatch.setattr(readout, "Reader", FakeReader)
    args = readout.parser().parse_args([
        str(tmp_path / "out.bin"), "--port", "test", "--resume",
        "--chunk-size", "65536", "--chunks-dir", str(directory)])
    result = readout.execute(args)
    assert result["device_verified"] is True
    assert [c[0] for c in calls] == ["read_flash", "verify_flash"]
    assert calls[0][1] == "65536"
    assert Path(result["output"]).read_bytes() == data


def test_resume_rejects_other_board(tmp_path, monkeypatch):
    directory, manifest, _ = fixture_chunks(tmp_path)
    manifest["idf_revision"] = "pin"
    readout.save_manifest(directory, manifest)

    class FakeReader:
        revision = "pin"

        def __init__(self, args, directory):
            pass

        def identify(self):
            return {"flash_size": 1048576}
    monkeypatch.setattr(readout, "Reader", FakeReader)
    args = readout.parser().parse_args([
        str(tmp_path / "out.bin"), "--port", "test", "--resume",
        "--chunks-dir", str(directory)])
    with pytest.raises(ValueError, match="mismatch"):
        readout.execute(args)


def test_short_successful_read_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(readout, "pinned_backend", lambda: (["python"], "pin"))
    chunk = tmp_path / "chunk.partial"
    calls = []

    def run(command, **kwargs):
        assert not chunk.exists()
        calls.append(command)
        chunk.write_bytes(b"a" * (2 if len(calls) == 1 else 65536))
        return subprocess.CompletedProcess(command, 0, b"success")
    monkeypatch.setattr(readout.subprocess, "run", run)
    args = readout.parser().parse_args(["out.bin", "--port", "test"])
    readout.Reader(args, tmp_path).run(
        "chunk", ["read_flash", "0", "65536", str(chunk), "--no-progress"])
    assert len(calls) == 2
    assert chunk.stat().st_size == 65536


@pytest.mark.parametrize("manifest", [[], None, {"schema_version": 1, "device": []}])
def test_malformed_manifest(manifest):
    with pytest.raises(ValueError):
        readout.validate_manifest(manifest)


def test_whole_flash_uses_longer_verification_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(readout, "pinned_backend", lambda: (["python"], "pin"))

    def run(command, **kwargs):
        assert kwargs["timeout"] == 300
        return subprocess.CompletedProcess(command, 0, b"verified")
    monkeypatch.setattr(readout.subprocess, "run", run)
    args = readout.parser().parse_args(["out.bin", "--port", "test"])
    assert readout.Reader(args, tmp_path).run(
        "verify", ["verify_flash", "0", "image.bin"]) == "verified"
