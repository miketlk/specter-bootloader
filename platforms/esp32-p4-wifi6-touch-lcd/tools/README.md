# ESP32-P4 Bootloader Tools

- [Install](#install)
- [Flash inspector](#flash-inspector)
  - [Usage](#usage)
  - [Input formats and coverage](#input-formats-and-coverage)
  - [Options](#options)
  - [JSON output and exit codes](#json-output-and-exit-codes)
  - [Boot analysis](#boot-analysis)
- [Tests](#tests)

This directory contains tools specific to the ESP32-P4 port. For upgrade
creation, signing, initial firmware generation and SD uploads, see the shared
[Bootloader Tools](../../../tools/README.md) documentation.

## Install

The flash inspector requires Python 3.10 or later and the repository's pinned
ESP-IDF checkout in `third_party/esp-idf/`. It uses that checkout's partition
and NVS parsers. A firmware build or activation of the ESP-IDF environment is
not required.

Run these commands from the repository root to create an isolated environment
and install the platform tools' dependencies with hash checking:

```sh
python3 -m venv build/flash-inspect-venv
. build/flash-inspect-venv/bin/activate
python -m pip install --require-hashes -r platforms/esp32-p4-wifi6-touch-lcd/tools/requirements.txt
```

These requirements are separate from the shared signing tools' requirements.
To update dependencies, edit [requirements.in](requirements.in), then regenerate
[requirements.txt](requirements.txt) using Python 3.10 and `pip-tools`:

```sh
python -m pip install pip-tools
python -m piptools compile --generate-hashes \
  --output-file platforms/esp32-p4-wifi6-touch-lcd/tools/requirements.txt \
  platforms/esp32-p4-wifi6-touch-lcd/tools/requirements.in
```

## Flash inspector

[flash-inspect.py](flash-inspect.py) inspects an already-acquired flash snapshot
from either supported board, `lcd-4p3` or `lcd-5`, and produces a versioned JSON
report. It inventories the observed partition table, Root Loader, both Specter
Bootloader slots, Main Firmware, approval trailers, shared boot journal, NVS,
key partition and unallocated ranges.

The report includes ESP image descriptors, embedded Specter versions, image
checksums and hashes, approval CRCs and image consistency, journal traversal,
and the `specter` namespace's `approval_seq`, `floor_boot` and `floor_main` U32
values. Unrelated NVS values and encryption key bytes are not included.

The tool operates offline and leaves its input unchanged. Flash acquisition,
device resets, flashing, decryption and repair are outside its scope. Approval
records preserve an installation decision but do not contain the original
upgrade signatures; the inspector does not verify signatures.

### Usage

The following examples run from the repository root with the environment above
active. File names are placeholders for snapshots you have already acquired.

Inspect a binary snapshot with an explicit board expectation:

```sh
python platforms/esp32-p4-wifi6-touch-lcd/tools/flash-inspect.py dump.bin \
  --board lcd-5 --pretty
```

Inspect a partial binary beginning at the `boot_a` offset:

```sh
python platforms/esp32-p4-wifi6-touch-lcd/tools/flash-inspect.py boot-a.bin \
  --format bin --base-offset 0x20000 --board lcd-5
```

Inspect Intel HEX and save a report for a validation gate:

```sh
python platforms/esp32-p4-wifi6-touch-lcd/tools/flash-inspect.py dump.hex \
  --format intelhex --strict > report.json
```

Show all command-line options:

```sh
python platforms/esp32-p4-wifi6-touch-lcd/tools/flash-inspect.py --help
```

The launcher also works from another working directory when invoked by its path.
Default configuration and layout paths are resolved relative to the package.

### Input formats and coverage

Supported formats are raw binary (`bin`), Intel HEX (`intelhex`), plain byte-pair
text (`hex`), ordinary addressed `xxd` output and `hexdump -C` output (`hexdump`).
The default `auto` mode recognizes text grammars rather than relying on file
extensions. Malformed recognizable text produces an input error.

Binary bytes consisting entirely of printable hex characters are ambiguous in
auto mode. Use `--format bin` to preserve them as binary. Plain hex must contain
complete byte pairs; arbitrary non-hex characters are not discarded. ASCII
columns in addressed dumps are not decoded as data.

`--base-offset` applies only to binary and plain hex. Addressed formats already
contain absolute addresses and reject a supplied base, including zero. Intel HEX
checksums, record lengths and EOF are validated. Overlapping input ranges are
rejected even when their bytes agree. Compressed hexdump `*` lines require an
unambiguous preceding full line and following address.

Missing ranges remain unknown, not erased. Each region reports complete, partial
or absent coverage separately from population. Only a fully covered all-`0xff`
region is classified as erased. An invalid or unreadable table permits recovery
inspection using explicitly labeled expected geometry; it does not turn that
geometry into an observed valid table. Input extent does not establish physical
flash capacity.

### Options

| Option | Meaning / default |
| --- | --- |
| `--format FORMAT` | `auto`, `bin`, `intelhex`, `hex`, `xxd` or `hexdump`; default `auto` |
| `--base-offset INT` | Binary/plain-hex start address; default zero |
| `--partition-table-offset INT` | Table address; default from [sdkconfig.defaults](../sdkconfig.defaults), currently `0x10000`; no automatic table scanning |
| `--expected-layout PATH` | Expected CSV; default [partitions.csv](../partitions.csv). An override changes expected geometry, not the supported ABI or boot-policy revision |
| `--board BOARD` | `auto`, `lcd-4p3` or `lcd-5`; default `auto`. An explicit value is an expectation and preserves observed platform strings |
| `--content-mode MODE` | `auto`, `plaintext` or `ciphertext`; default `auto` |
| `--pretty` | Indent JSON output |
| `--strict` | Return exit 1 for failed or incomplete validation |
| `--max-span INT` | Maximum represented address span in bytes; default 64 MiB |
| `--max-source-size INT` | Maximum source file size in bytes; default 512 MiB |
| `--max-input-ranges INT` | Text record limit; default 262144 |
| `--version` | Print the tool version |

Integer options accept decimal or `0x` hexadecimal values. Addresses remain
within a 32-bit address space even when resource limits are overridden.

Content mode records the caller's interpretation, not hardware encryption state.
Auto mode does not infer ciphertext from entropy or missing magic. Explicit
ciphertext mode skips encrypted metadata interpretation while permitting the
current plaintext journal to be examined through expected recovery geometry.
Use plaintext mode for bytes known to be plaintext, including externally
decrypted snapshots. Partition flags alone do not establish the input's state.

### JSON output and exit codes

Inspections and errors produce one JSON object followed by a newline on stdout.
Help and version are text exceptions. The report follows
[report-v1.schema.json](flash_inspect/schema/report-v1.schema.json), with
`schema_version: 1`. Output is deterministic for identical inputs, options and
tool/backend versions.

Top-level fields include input and backend provenance, expectations, coverage,
partition table, regions, conditional `boot_analysis`, coded `findings` and a
`summary`. Report `status` is `ok`, `findings`, `partial` or `error`;
`summary.validation` is `pass`, `fail` or `incomplete`. Individual checks use
`pass`, `fail`, `unknown`, `not_applicable` or `unsupported`; unknown is not false.
Offsets and sizes are integer byte values. Digests are lowercase hexadecimal.

Consumers should branch on schema version, check status and finding codes, such
as `RANGE_MISSING`, `LAYOUT_MISMATCH`, `IMAGE_HASH_MISMATCH` or
`APPROVAL_CRC_MISMATCH`, rather than diagnostic prose.

| Exit | Meaning |
| --- | --- |
| 0 | Inspection completed; findings and partial coverage may still be present |
| 1 | `--strict` was supplied and validation failed or is incomplete |
| 2 | Invocation, input, configuration or required-dependency error |
| 3 | Unexpected internal failure; output contains a sanitized error |

Strict validation requires complete supported evidence for the table, Root,
all three application slots, both journal sectors and project NVS. Missing
application padding therefore makes strict validation incomplete. An erased
unused app slot does not itself fail validation when a healthy boot path exists.
Unused flash tails and an absent optional `main_aux` are not mandatory evidence.

Record detail is bounded: at most 128 version tags per image, 1024 NVS pages,
128 project namespace records and 512 project counter records. Truncation is
explicit and unresolved values remain unknown. Every occupied journal slot is
included, up to 128 records in the supported two-sector layout.

### Boot analysis

Boot analysis models a normal reset without a valid retained RTC request. It
uses approval and journal state to compare confirmed bootloaders and unattempted
trials by version and approval sequence. Attempted, unconfirmed trials are
excluded; a trial also requires capacity for an attempted marker. Journal
traversal stops at the first erased slot, and later forensic records are marked
as ignored by firmware. `append_available` reports whether traversal reaches a
known erased slot (`true`), proves the sector full (`false`), or encounters
missing evidence first (`null`). Total `free_capacity` remains `null` for an
incomplete sector, even when an append slot is known.

Main Firmware is reported separately: it is not a normal-reset fallback and
requires a valid matching RTC handoff request. Missing Main evidence affects
strict completeness but does not block independently determined normal selection.
Root entry and load ranges are checked against the pinned platform's SRAM map;
checksum/hash checks remain separate from executable address checks. Malformed
embedded version tags are reported as invalid, with their physical offsets.
Project consistency checks, including version bounds, remain separate from
Root's rules. `NVS_VERSION_BELOW_INSTALL_FLOOR` is informational: the persisted
installation history can exceed an installed image's version, especially for
the older confirmed recovery bootloader or after an interrupted installation.
It does not invalidate an existing approval or affect Root selection, and does
not fail strict validation (older inspector reports called it
`NVS_FLOOR_CONFLICT` and incorrectly treated it as an error).

Approval `padding_canonical` recognizes both current writers: an erased
four-byte prefix word, followed by zero commit padding from firmware or erased
commit padding from initial USB provisioning. Other padding remains diagnostic;
CRC and Root eligibility checks still apply independently.

`UNALLOCATED_POPULATED` remains a strict validation error when supplied bytes
outside the declared layout are not erased. This detects residual content, not
necessarily firmware corruption: flashing selected images does not erase the
rest of a previously used board. Inspect and back up such ranges before an
explicitly authorized cleanup. The inspector never erases them.
Ambiguous NVS duplicates, rollover and damaged pages are not converted into
valid zero counters.

A prediction does not identify the running slot or prove that the next boot
will succeed. Retained RTC state, eFuses, hardware revision, runtime restrictions,
journal write success and hardware health are not established by an offline
snapshot. Incomplete or unsupported evidence can leave selection unknown.

## Tests

With the platform tools environment active, run from the repository root:

```sh
python -m pytest -c tools/pytest.ini \
  platforms/esp32-p4-wifi6-touch-lcd/tools/flash_inspect/test/ -q
```

Tests generate temporary fixtures and run offline without boards or firmware
builds. They cover input normalization, sparse coverage, decoders, JSON Schema,
CLI exits and conditional policy. A host C compiler enables the ABI/CRC and
firmware-policy differential checks; `xxd` enables comparisons with real command
output. Tests requiring either executable are skipped when it is unavailable.
