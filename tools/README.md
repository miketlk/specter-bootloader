# Bootloader Tools

- [Bootloader Tools](#bootloader-tools)
  - [Install](#install)
  - [Test key generation](#test-key-generation)
  - [Upgrade file generator](#upgrade-file-generator)
    - [**gen** command](#gen-command)
    - [**sign** command](#sign-command)
    - [**message** command](#message-command)
    - [**import-sig** command](#import-sig-command)
    - [**dump** command](#dump-command)
  - [Creation of initial firmware](#creation-of-initial-firmware)

## Install

These tools are developed and tested in isolated Python environment. To create an empty environment using virtualenv run (from the root project directoty):

```bash
virtualenv .venv
```

Python dependencies can installed with hash checking by running:

```bash
pip install --require-hashes -r requirements.txt
```

To update requirements.txt with hash generation use:

```bash
pip freeze > requirements.in
pip-compile requirements.in --generate-hashes --allow-unsafe
```

## Test key generation

To generate a test key for signing upgrade file use:

```bash
openssl ecparam -name secp256k1 -genkey -noout -out mykey.pem
```

To encrypt a newly generated private key use:

```bash
openssl ec -aes256 -in mykey.pem -out mykey_enc.pem
```

Or, better, use a single command to avoid writing plaintext key to disk:

```bash
openssl ecparam -name secp256k1 -genkey | openssl ec -aes256 -out mykey.pem
```

To inspect generated key use:

```bash
openssl pkey -in mykey.pem -text
```

## Upgrade file generator

To generate and sign an upgrade file use the provided tool: `upgrade-generator.py`. It creates a signed container with binary images of the Bootloader or of the Main Firmware or both.

Contents of a produced upgrade file determine which keys are authorized to sign this upgrade. If an upgrade file contains the Bootloader, only the Vendor keys can be used to sign it. If only the Main Firmware is contained inside an upgrade file, it can be signed with either Vendor keys or Maintainer keys or any mix of both.

Upgrade generator supports three commands:

- [**gen**](#gen-command) - generate an upgrade file, with optional signing
- [**sign**](#sign-command) - add a signature to an existing upgrade file
- [**message**](#message-command) - output a Bech32 message to sign externally
- [**import-sig**](#import-sig-command) - import an externally made signature
- [**dump**](#dump-command) - displays contents of an upgrade file

To get full usage instructions run `upgrade-generator.py <command> --help`.

ESP32-P4 `.bin` inputs are decoded with the esptool version installed by the
pinned ESP-IDF checkout. Activate that environment before generating an ESP32
upgrade or approval trailer:

```sh
. ./third_party/esp-idf/export.sh
. .venv/bin/activate
```

Activate the tools virtual environment after ESP-IDF so the packaging tools
retain their normal Python dependencies while locating the pinned ESP-IDF
Python environment for image validation.

The tools reject images with malformed segments or load ranges, a bad ESP
checksum, a missing application descriptor, the wrong chip ID, trailing bytes,
or a missing/incorrect appended SHA-256 before producing signable output.
Address checks use the pinned ESP-IDF loader's separate flash, PSRAM, RAM,
RTC and SPM ranges, excluding ROM and gaps. Padding cannot supply an entry
point. These host checks do not replace Root Loader's verification, including
checks for overlap with its own stack and linked sections.

### **gen** command

```console
$ upgrade-generator.py gen --help
Usage: upgrade-generator.py gen [OPTIONS] <upgrade_file.bin>

  Generates an upgrade from Intel HEX or canonical ESP-IDF app images. It is
  required to specify at least one Main Firmware or Bootloader input.

  In addition, if a private key is provided it is used to sign produced
  upgrade file. Private key should be in PEM container with or without
  encryption.

Options:
  -b, --bootloader <file.hex>   Intel HEX file containing the Bootloader.
  --bootloader-bin <file.bin>   Canonical hash-appended ESP-IDF Bootloader
                                application image.
  --firmware-bin <file.bin>     Canonical hash-appended ESP-IDF Main Firmware
                                application image.
  -f, --firmware <file.hex>     Intel HEX file containing the Main Firmware.
  -k, --private-key <file.pem>  Private key in PEM container.
  -p, --platform <platform>     Platform identifier, i.e. stm32f469disco.
  --help                        Show this message and exit.
```

### **sign** command

```console
$ upgrade-generator.py sign --help
Usage: upgrade-generator.py sign [OPTIONS] <upgrade_file.bin>

  This command adds a signature to an existing upgrade file. Private key
  should be provided in PEM container with or without encryption.

  The signature is checked for duplication, and any duplicating signatures
  are removed automatically.

Options:
  -k, --private-key <filename.pem>
                                  Private key in PEM container used to sign
                                  produced upgrade file.  [required]

  --help                          Show this message and exit.
```

### **message** command

```console
$ upgrade-generator.py message --help
Usage: upgrade-generator.py message [OPTIONS] <upgrade_file.bin>

  This command outputs a message in Bech32 format containing payload
  version(s) and hash to be signed using external tools.

Options:
  --help  Show this message and exit.
```

### **import-sig** command

```console
$ upgrade-generator.py import-sig --help
Usage: upgrade-generator.py import-sig [OPTIONS] <upgrade_file.bin>

  This command imports an externally made signature into an upgrade file.
  The signature is expected to be a standard Bitcoin message signature in
  Base64 format.

Options:
  -s, --signature <signature_base64>
                                  Bitcoin message signature in Base64 format.
                                  [required]

  --help                          Show this message and exit.
```

### **dump** command

```console
$ upgrade-generator.py dump --help
Usage: upgrade-generator.py dump [OPTIONS] <upgrade_file.bin>

  This command dumps information regarding firmware sections and lists
  signatures with public key fingerprints.

Options:
  --help  Show this message and exit.
```

## Creation of initial firmware

To program a "clean" device a complete firmware image needs to be created, including at least the Start-up code and one copy of the Bootloader. The Main Firmware can be added-up as well to make the device fully operating right after programming.

> IMPORTANT: Initial firmware is not intended for distribution as it does not use signature verification!

For ESP32-P4 provisioning, the same tool can create the 4 KiB approval trailer
for a canonical application image. A provisioned boot role normally uses
`--confirmed` and a separate `--journal-output`; Main Firmware has no journal:

```sh
make-initial-firmware.py --esp32-app bootloader.bin \
  --esp32-platform esp32-p4-wifi6-touch-lcd-4p3 \
  --esp32-role boot_a --sequence 1 --confirmed \
  --journal-output boot_a.journal boot_a.trailer
```

The approval trailer still occupies the final 4 KiB of its application partition.
The journal output is a separate plaintext 4 KiB sector in `boot_journal`:
`boot_a` at `0x620000`, `boot_b` at `0x621000`. Generate and provision each
Bootloader slot's journal separately; do not encrypt these journal files.
Omitting `--confirmed` with `--journal-output` produces an erased trial journal.
The new layout requires the matching Root Loader, partition table, Bootloader
applications and journal sectors to be provisioned together; old trailer-local
journals are not migrated by a field upgrade.

The recommended way to create an initial firmware is by the help of `make-initial-firmware.py` tool. Usage instructions can be obtained by running it with `-help` option:

```console
$ make-initial-firmware.py --help
Usage: make-initial-firmware.py [OPTIONS] <output_file_name>

  This command makes a firmare file for initial programming of a "clean"
  defice. The firmware file is made by combining together the Start-up code,
  the Bootloader, and, optionally, the Main Firmware.

Options:
  -s, --startup <file.hex>     Intel HEX file containing the Start-up code.
                               [required]

  -b, --bootloader <file.hex>  Intel HEX file containing the Bootloader.
                               [required]

  -f, --firmware <file.hex>    Intel HEX file containing the Main Firmware.
  -bin, --bin-output           Outputs firmware in raw binary format.
  --help                       Show this message and exit.
```

## ESP32-P4 SD uploader

`sd-uploader.py` stages files on a board's installed FAT32 microSD card through
an independent, internal-RAM ESP-IDF application. It preserves unrelated files
and refuses destination collisions. Package signing and installation remain the
normal bootloader's responsibility.

Activate the pinned ESP-IDF Python environment, install the optional dependencies
with hashes, and [build the device application](../platforms/esp32-p4-wifi6-touch-lcd/sd_uploader/README.md).
The uploader needs Python 3.11 or later; its requirements are separate from the
legacy signing requirements. ROM operations currently require esptool 4.12.0.

For routine development, configure an authorized [version-2 fixture](sd_uploader/fixtures/fixture-development.example.json)
once, then use one command per build:

```sh
export SD_UPLOADER_FIXTURE=/path/to/dut.json
python tools/sd-uploader.py doctor
python tools/sd-uploader.py stage build/specter_upgrade.bin
python tools/sd-uploader.py stage build/specter_upgrade.bin --replace --format text
# Requires normal-boot in the fixture's allowed_resets:
python tools/sd-uploader.py stage build/specter_upgrade.bin --replace --after boot
```

An explicit `--fixture` overrides the environment variable. Declaration paths
are relative to the declaring JSON file; environment paths are relative to the
invocation directory. No global or last-used device is selected. `devices`
enumerates CH343 serial numbers, locations and current ports without opening
serial. `doctor` audits dependencies, interpreter, RAM image and enumeration;
it does **not** verify the physical chip or card. Neither command resets hardware.
Enrollment still requires the explicitly requested ROM probe and RAM-service
HELLO workflow described below. Never set `authorized` for an unverified fixture.

`stage` defaults to the source basename, which must match the firmware's
case-sensitive `specter_upgrade*.bin` candidate rule. `--name` overrides it.
Transport accepts deliberately malformed images; it does not validate signing.
Exactly one candidate is required. Unexpected candidates fail before mutation.
An identical destination succeeds only after full SD length/digest verification.
A different destination requires `--replace`, which permits only that exact FAT
name (case insensitive). Replacement removes then uploads; it is **not atomic**
and interruption can leave the old file absent. Recovery restores the requested
new bytes, not the previous file. Unrelated entries are preserved. A same-size
mismatch requires a RAM reload because wire-v1 VERIFY enters UNCERTAIN on failure.
Use `run-case` for multi-file candidate sets and explicit qualification assertions.

Version 1 fixtures remain supported with pinned NOR baselines. Version 2 adds
`preservation: "pinned"|"current"` and `allowed_resets`. Pinned policy requires
`starting_state`; current policy requires `starting_state: null`, captures full
NOR on every run, and checks equality before normal boot. It proves staging
preservation, not starting-firmware correctness. `run-case` always requires pinned
policy. ROM-only operations need `rom-entry` and `rom-verify`; add `normal-boot`
only when authorized. There is no fallback from a failed pinned comparison.

The default end state is ROM download mode. `--after boot` releases the SD card,
compares complete NOR, then requests one normal reset and releases serial. Its
result is `boot-requested`, **not** installation or health confirmation. A harness
needing the first boot bytes must arm capture before reset; use the existing
in-process approved-mock `run-case` observation for that assertion. `release`
alone never boots the board. Failures never normal-boot as cleanup.

`--dry-run` validates local inputs without device access or output/artifact writes.
New runs are reserved under the bridge lock, by default in
`build/sd-uploader/runs/<timestamp>-<random>/`, or at an explicit new `--run DIR`.
The result returns the run path. Inputs, ELF/image/sdkconfig, audit source inputs,
and pinned baselines are copied independently into `build/sd-uploader/artifacts/`
before hardware access. Evidence points to those snapshots, so rebuilding the
original paths does not change recovery inputs. Keep both directories while a
run is incomplete. To clean completed runs, explicitly remove their run directory
and the artifact directory referenced by `fixture.json`; do not prune interrupted
runs. `make clean` may remove build outputs, so recover before cleaning.

```sh
python tools/sd-uploader.py result --run RUN_DIRECTORY
python tools/sd-uploader.py recover --fixture /path/to/dut.json --run RUN_DIRECTORY
```

For incomplete runs, `result` derives phase, device state, and recovery instructions
from durable `state.json`; an earlier failure is retained as `previous_failure`.

Recovery revalidates identity and full NOR, verifies uncertain published files,
and cleans only recorded session temporary names. Completed recovery is an
offline result read. If normal reset may have occurred, recovery refuses to replay
staging or reset and requests external observation. Missing evidence after possible
media access also fails closed, including corrupt NOR baselines. Before media
access, a missing or interrupted baseline can be captured again. Probe output is
parsed before atomic publication. Regional NOR hashes remain diagnostics based
on the current host partition layout; preservation compares device identity,
security information, and the full-capacity NOR digest independently of that layout.
Older runs without the new durable `state.json` require manual reconciliation; old mutable-input recovery is no longer replayed.
Existing fixture/case schemas and low-level command arguments remain accepted.

All commands now return one compact JSON terminal result on stdout with
`schema_version: 1`; this replaces the previous unversioned pretty JSON and
stderr-only failures. `--format text` selects concise human output. Failures have
`code`, `phase`, `message`, `device_state`, and a run path when reserved. A recovery
action is included only when supported by durable state. Raw HELLO, receipts,
serial data and tracebacks belong in evidence; `--verbose` also prints diagnostics
to stderr. `list --candidates --pages 16 --cursor 0` bounds returned wire pages
(eight entries per page), reports `truncated`, and supplies `next_cursor`.
Candidate filtering never hides continuation. Help and offline `result` work
without optional hardware packages.

| Exit | Stable codes (examples) | Meaning / action |
| --- | --- | --- |
| 0 | — | Success or validated dry run |
| 2 | `USAGE`, `CONFIG`, `DEPENDENCY_MISSING`, `INTERPRETER_MISMATCH`, `INVALID_NAME`, `RESET_NOT_AUTHORIZED`, `ARTIFACT_MISMATCH`, `DOCTOR_FAILED` | Fix inputs or use the declared interpreter |
| 3 | `DEVICE_SELECTION`, `LOCKED`, `IDENTITY_MISMATCH` | Resolve endpoint, ownership or fixture identity |
| 4 | `DEST_EXISTS`, `CANDIDATE_CONFLICT`, `TEMP_CONFLICT`, `NOR_MISMATCH`, `DIGEST_MISMATCH`, `VERIFY_MISMATCH`, `EVIDENCE_INCOMPLETE`, `HANDOFF_REQUIRED` | Inspect evidence; do not infer permission to retry or replace |
| 5 | `TIMEOUT`, `TRANSPORT`, `ROM_TRANSPORT` | Inspect durable state before recovery |
| 130 | `INTERRUPTED` | SIGINT/SIGTERM handled best effort |
| 1 | `INTERNAL` | Unexpected failure; inspect diagnostics |

`--timeout` is the whole operation deadline (default 900 seconds), separate from
case observation timeout. This leaves conservative room for two full 32 MiB NOR
scans (about two minutes each), RAM loads, and ordinary multi-megabyte transfers;
large/slow transfers can request more time. The deadline covers host work, ROM
subprocesses, serial waits, retries and observation. POSIX CLI cancellation allows
up to five additional seconds for descriptor/process cleanup and best-effort
journaling; no cleanup media mutations or normal resets are attempted. SIGKILL
and power loss rely on the journal. `--progress auto` prints only on a stderr TTY;
`off` is silent and `json` emits stderr phase events plus at most one heartbeat
per 30 seconds. No per-chunk output appears on stdout.

For an **already running** UART uploader:

The default application/CLI rate is 4000000 baud. Explicit rates are 115200,
460800, 921600, 2000000, 3000000, and 4000000; use `--baud` with the
corresponding device build. ROM recovery always begins at 115200; the loader negotiates its
RAM download rate and selects the application rate from the image manifest.

```sh
python tools/sd-uploader.py status --port PORT --board lcd-4p3 \
  --session 0102030405060708090a0b0c0d0e0f10 --save-identity /tmp/sdu-identity.json
python tools/sd-uploader.py list --port PORT --board lcd-4p3 \
  --session 0102030405060708090a0b0c0d0e0f10
python tools/sd-uploader.py upload --port PORT --board lcd-4p3 \
  --session 0102030405060708090a0b0c0d0e0f10 --identity /tmp/sdu-identity.json \
  fixture.dat --name development-fixture.dat
python tools/sd-uploader.py verify --port PORT --board lcd-4p3 \
  --session 0102030405060708090a0b0c0d0e0f10 fixture.dat --name development-fixture.dat
python tools/sd-uploader.py release --port PORT --board lcd-4p3 \
  --session 0102030405060708090a0b0c0d0e0f10 --identity /tmp/sdu-identity.json
```

Select a fresh nonzero 16-byte session token for each RAM boot and reuse it
between commands. The example token is illustrative. One session owns the
service until reset. Identity files bind mutations to board, build, chip and
normalized card CID. The importable `sd_uploader.client.Client` implements the
same operations. `remove NAME` removes one regular file; `abort` cancels an
incomplete transaction. Names are root basenames, at most 128 printable ASCII
bytes, without FAT-illegal characters or the reserved `_sdu_` prefix.

Ordinary commands never reset or load the board. `run-case` owns the full
lifecycle, with an explicitly authorized fixture, qualified CH343 reset control,
RAM manifest, chip/card identity and read-only starting-state NOR evidence:

```sh
run_root=$(mktemp -d "${TMPDIR:-/tmp}/specter-sd-uploader.XXXXXX")
python tools/sd-uploader.py run-case --fixture FIXTURE.json --case CASE.json \
  --run "$run_root/example" --dry-run
python tools/sd-uploader.py run-case --fixture FIXTURE.json --case CASE.json \
  --run "$run_root/example"
```

Run evidence is retained under `$run_root`; copy it elsewhere if it must survive
temporary-directory cleanup.

Start from the [fixture](sd_uploader/fixtures/fixture.example.json) and
[case](sd_uploader/fixtures/case.example.json) examples. Replace all placeholders;
the example fixture is deliberately unauthorized. Paths in declarations are
relative to their JSON file. A case's `starting_state_id` is the SHA-256 of its
starting-state JSON; that JSON contains ROM identity/security and full-NOR hashes.
A read-only ROM probe is available as `python -m sd_uploader.rom_probe` with
`PYTHONPATH=tools`; `--reset` explicitly enters download mode and `--hashes`
collects the full detected NOR capacity and canonical region hashes.

`run-case` requires a new evidence directory (even an empty existing directory
is rejected). It atomically reserves that directory after acquiring the bridge
lock; use `recover` to reopen an interrupted run. Rejected lock acquisitions do
not write evidence.

The runner locks the identified bridge, checks security and starting NOR,
loads the audited image with no stub, and keeps the same serial descriptor open
through HELLO. It checks chip/build/card before staging, removes only explicitly
named files, verifies every upload and the final upgrade-candidate set, releases
SD, and compares all NOR before any normal boot. ROM MD5 detects accidental NOR
changes; it is not release authentication. No flash writes, erase, provisioning,
restoration, or eFuse changes are part of the service.

`outcome: "stage-only"` leaves the board in ROM after comparison.
`outcome: "approved-mock"` additionally performs the declared normal hardware
reset and requires current-window, approval-aware mock telemetry. Its observation
specifies numeric approved `version`, hex `build` (mock payload SHA-256), and
`bloat`. Missing telemetry is a timeout. Negative and trial/fallback assertions
without specific loader observations are rejected as unsupported. DTR/RTS reset
is not a power cut; true power-cut tests need a separately qualified controller.

Host journals, raw serial bytes, identity, file receipts, NOR hashes and outcomes
are saved in the run directory. After interruption, `recover --fixture ...
--run ...` requires the same checked image and unchanged NOR, reloads RAM,
identifies the card, removes only exact reserved uploader temporary filenames,
and verifies or recreates every declared file. It refuses to overwrite an unrelated original
destination. It checks the complete candidate set, releases SD, and compares NOR
again before following the case's declared boot outcome. An old receipt or
filename alone never proves an interrupted fixture is valid.

SPSD v1 uses `SPSD | 01 00 | payload_length:u32be | canonical CBOR | crc32:u32be`.
CRC-32/ISO-HDLC covers the header after magic and the payload. Maximum payload is
18432 bytes; WRITE chunks are at most 16384 bytes. Maps have text keys, deterministic
length-first order, definite lengths and unsigned integer fields. The parser
rejects duplicates, excessive depth/size, malformed UTF-8, tags, floats, indefinite
lengths and trailing CBOR. Complete valid `SPMF` telemetry frames are demultiplexed
without searching inside their payloads. Requests retry identical bytes with
bounded deadlines; device boot nonces invalidate stale sessions.

Uploads default to the device's advertised maximum chunk. For comparisons, use
`upload ... --chunk-size 4096 --evidence RUN_DIRECTORY`, then
`python tools/sd-uploader.py profile --run RUN_DIRECTORY`. The report separates
local hashing, CBOR encoding, serial-write time, request latency, device framing
and command work, flush/sync/close, temporary readback, publication, and final
readback. Residual host/wait time includes serial receive, scheduling and evidence
I/O; serial-write return time alone is not physical UART transmission time.
Both host and device read available bytes without waiting to fill a large buffer.

Run the host suite with `python -m pytest -q tools`. Uploader tests live in
`sd_uploader/test/`, retaining the `*_test.py` discovery convention. These modules
explicitly skip when their optional Python dependencies are absent. For a
dedicated uploader test job, install those dependencies and require their imports
before running tests so missing packages cannot silently skip coverage:

```sh
python -m pip install --require-hashes -r tools/requirements-sd-uploader.txt
python -c "import cbor2, elftools, serial" && python -m pytest -q tools/sd_uploader/test
```

The test environment also needs pytest. The storage fault harness
uses a host C compiler, OpenSSL development files via `pkg-config`, and address/
undefined-behavior sanitizers. Linked-image corruption tests additionally require
the `lcd-4p3` RAM build. UART hardware qualification uses the real board and card;
ESP32-P4 QEMU is not a substitute. The serial backend currently targets macOS and
POSIX hosts; each bridge/host reset behavior requires qualification.
