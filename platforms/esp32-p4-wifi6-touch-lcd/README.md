# ESP32-P4 Waveshare Platform

This directory contains the ESP-IDF Specter Bootloader application, immutable
Root Loader, board support, and Mock Main Firmware fixture for the Waveshare
4.3-inch and 5-inch ESP32-P4 boards. The Root Loader accepts only the three
fixed application roles and exact, committed approval records.

## Pinned build environment

All project commands go through `tools/idf.sh`. The wrapper rejects an ambient
`IDF_PATH`, verifies that `third_party/esp-idf` matches the repository gitlink,
rejects a dirty ESP-IDF checkout, exports that checkout, and invokes its
`idf.py`. The pinned release is ESP-IDF v5.5.5 at
`b774170ff46c393eeb5e495ea37936038d3f4f4f`.

Initialize a checkout recursively, install the matching toolchain once, and
build a validation target:

```sh
git submodule update --init --recursive
third_party/esp-idf/install.sh esp32p4
platforms/esp32-p4-wifi6-touch-lcd/tools/build.sh \
  plaintext-dev boot-a build size
```

The build profiles are:

- `plaintext-dev`: the only development and hardware-bring-up profile. It
  rejects flash encryption, Secure Boot, anti-rollback, ROM-download changes,
  JTAG changes, and ROM-log eFuse changes.
- `encrypted-production`: a compile-only release profile during development.
  It enables settings that can burn irreversible security eFuses when booted
  on hardware. Do not flash this profile during bring-up.

There is no encrypted development profile because ESP32-P4 hardware flash
encryption requires irreversible eFuse programming.

## Root Loader selection

The Root Loader checks every allowed partition's exact label, type, subtype,
offset, and size; rejects `otadata` and unexpected application partitions; and
gives the stock ESP-IDF handoff path an isolated state containing only the
selected partition. It verifies the committed role, platform, version, exact
image length, appended ESP-IDF SHA-256, and approval CRC before loading an
image.

The generated flash metadata places the validation application at:

| Role | Offset |
| --- | ---: |
| `boot_a` | `0x020000` |
| `boot_b` | `0x120000` |
| `main` | `0x220000` |

Ordinary resets select a confirmed Specter Bootloader. A newer approved copy
gets one durable trial; it must restart through the Root Loader to confirm
itself, otherwise the next reset falls back to the older confirmed copy.
Main Firmware is loaded only through a CRC-protected software-reset request
from Specter Bootloader. Root Loader and the partition table are not field
upgrade targets.

Run the complete local compile, size, layout, configuration, dependency, and
Root Loader checks with:

```sh
platforms/esp32-p4-wifi6-touch-lcd/tools/validate.sh
```

Run `tools/diff-root-loader.sh` to inspect the complete delta from the pinned
stock `bootloader_start.c`.

## Flash layout

The image uses a portable 16 MiB logical flash window. Both validated boards
have 32 MiB physical flash, but capacity above the logical window remains
unused so one release image works on every supported board with at least
16 MiB.

`partition_layout.cmake` is the single source for application partition sizes.
The build derives the offsets, generates a build-local partition CSV, and
compiles the same values into the Root Loader allow-list.

| Role | Type/subtype | Offset | Partition size | Maximum image |
| --- | --- | ---: | ---: | ---: |
| `boot_a` | `app,factory` | `0x020000` | 1 MiB | 1 MiB - 4 KiB |
| `boot_b` | `app,ota_0` | `0x120000` | 1 MiB | 1 MiB - 4 KiB |
| `main` | `app,ota_1` | `0x220000` | 4 MiB | 4 MiB - 4 KiB |
| `main_aux` | `data,0x40` | `0x620000` | 0 (disabled) | not emitted |

The final 4 KiB sector of each application partition is reserved outside the
ESP-IDF image for its approval record and, for bootloader slots, the trial
journal. `main_aux` is a provision for a future extension. While its canonical
size is zero, the layout generator drops it from the ESP-IDF partition CSV, so
it reserves no flash and is not present on devices. Enabling it later requires
assigning a nonzero size and reviewing the resulting layout. It is not a second
Main Firmware slot or a TEE partition.

ESP32-P4 fixes the Root Loader offset at `0x2000`. The partition table is at
`0x10000`, leaving 56 KiB for the second stage and keeping `boot_a` aligned at
`0x20000`. Every build checks the generated Root Loader against this limit.

## Validated toolchains

The release comparison was run with clean temporary project and build trees;
the archived Waveshare sources and pinned submodule were not modified.

| ESP-IDF | Display-example result | 4.3-inch image | 5-inch image |
| --- | --- | ---: | ---: |
| v5.5.4 (`735507283d5b2f9fb363a1901172dbd9e847945d`) | both build unchanged | 292,998 B | 259,232 B |
| v5.5.5 (`b774170ff46c393eeb5e495ea37936038d3f4f4f`) | both build unchanged | 295,050 B | 260,896 B |
| v6.0.2 (`7101770dc6db2667b3c477cc31365dd1acd6db4e`) | both build with a disposable compatibility shim | 264,388 B | 230,442 B |

The v6.0.2-only shim was applied to temporary copies. It removed the obsolete
ESP-IDF unit-test component path, added `espressif/usb` 1.5.0, declared the
split GPIO, I2C, I2S, SPI, SDMMC, and LEDC dependencies, and translated LCD
configuration fields removed in ESP-IDF 6. These results prove source
compatibility after narrow adaptations, not display operation on hardware.

The pinned v5.5.5 project uses `riscv32-esp-elf-gcc` 14.2.0. The three
plaintext target variants and the compile-only encrypted-production variant
build successfully. The validation application is `0x2e510` bytes plaintext
and `0x2fdb0` bytes encrypted. Root Loader is `0x59c0` bytes for plaintext
`boot_a`, `0x59d0` bytes for the plaintext OTA roles, and `0x8a30` bytes for
encrypted `boot_a`.

ESP-IDF v5.5.5 does not support ESP32-P4 QEMU. QEMU therefore cannot provide a
CPU-startup smoke test for the pinned release and, if support is added later,
will not replace physical validation of MIPI DSI, GT911, backlight, SDMMC,
USB-UART wiring, flash capacity, or PSRAM.

## Hardware validation

Plaintext exact-load validation completed on both supported boards. No eFuses
were burned; Secure Boot and flash encryption remained disabled.

| Board | Chip revision | Configured flash | Physical flash | PSRAM | Exact-load roles |
| --- | --- | ---: | ---: | ---: | --- |
| Waveshare 4.3-C | ESP32-P4 v1.3 | 16 MiB | 32 MiB | 32 MiB | `boot_a`, `boot_b`, `main` |
| Waveshare 5-C | ESP32-P4 v1.3 | 16 MiB | 32 MiB | 32 MiB | `boot_a`, `boot_b`, `main` |

All six runs reached the selected partition and emitted
`SPECTER_PHASE2_OK`. This validates flash sizing, PSRAM initialization, and
Root Loader handoff; display, touch, and SDMMC still require their dedicated
board-support validation.

## Mock Main Firmware

The mock is an ESP32-P4 port-testing fixture, not product firmware and not a
wallet. It builds only with the `plaintext-dev` profile for the fixed `main`
role and uses test keys. Select a board and an exact flash-resident filler size
with the stable root interface:

```sh
make esp32-p4-wifi6-touch-lcd-mock BOARD=lcd-4p3 MOCK_BLOAT_SIZE=0
make esp32-p4-wifi6-touch-lcd-mock BOARD=lcd-5 MOCK_BLOAT_SIZE=1048576
```

Set `SPECTER_MOCK_VERSION=major.minor.patch` when producing a newer payload for
an upgrade test. Major is limited to 41; minor and patch are limited to 999,
matching `BL_VERSION_MAX`. The build embeds the matching Specter `tag10`
version and ESP application descriptor version.

Each build writes a machine-readable `mock-manifest.json` beside the mock ELF,
map, and canonical hash-appended binary. The post-link checker rejects an
incorrect or non-loadable filler section and any image that overlaps the
reserved approval trailer. Determine and rebuild the maximum fitting filler
with:

```sh
platforms/esp32-p4-wifi6-touch-lcd/tools/find-max-mock-bloat.sh 4p3
```

Install the pinned host decoder dependencies and capture framed CBOR from the
Type-C connector labelled `USB TO UART` (115200 baud, 8-N-1):

```sh
python -m pip install --require-hashes \
  -r platforms/esp32-p4-wifi6-touch-lcd/mock_app/tools/requirements.txt
python platforms/esp32-p4-wifi6-touch-lcd/mock_app/tools/mock_telemetry.py \
  --port /dev/cu.usbmodemXXXX --board lcd-4p3 --bloat 0 --pretty
```

Generate a normal M-of-N-signed Main Firmware upgrade package from the mock
binary with the shared tool and copy it to the microSD root:

```sh
tools/upgrade-generator.py gen \
  --firmware-bin build/.../specter_esp32p4_mock_main.bin \
  --platform esp32-p4-wifi6-touch-lcd-4p3 \
  --private-key maintainer.pem specter_upgrade_mock.bin
```

Additional signatures use the unchanged `sign`, `message`, and `import-sig`
commands. Direct development flashing remains useful for display and UART
bring-up, but does not prove the approved Root Loader or microSD path.

The `encrypted-production` profile is compile-only by default because first
boot can burn irreversible security eFuses. Passing any IDF flash command with
that profile requires the separate, explicit
`SPECTER_ALLOW_IRREVERSIBLE_HARDWARE=1` environment opt-in.
