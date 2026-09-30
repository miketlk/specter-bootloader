# ESP32-P4 Waveshare Platform — Phase 2 Validation

This directory contains the Phase 2 toolchain and Root Loader validation
project for the Waveshare 4.3-inch and 5-inch ESP32-P4 boards. It is not yet a
complete Specter Bootloader port: platform syscalls, approval trailers,
trial/confirmation state, SDMMC, display, and touch are later phases.

## Pinned build entry points

All commands go through `tools/idf.sh`. The wrapper rejects an ambient
`IDF_PATH`, verifies that `third_party/esp-idf` matches the repository gitlink,
rejects a dirty IDF checkout, exports that checkout, and invokes its `idf.py`.
The current gitlink is ESP-IDF v5.5.5
(`b774170ff46c393eeb5e495ea37936038d3f4f4f`).

Initialize a checkout recursively, install the matching tools once, and build:

```sh
git submodule update --init --recursive
third_party/esp-idf/install.sh esp32p4
platforms/esp32-p4-wifi6-touch-lcd/tools/build.sh plaintext-dev boot-a build size
```

Profiles are composed from the checked-in defaults:

- `plaintext-dev`: the only development/hardware-bring-up profile. It keeps
  flash encryption, Secure Boot, anti-rollback, ROM-download changes, JTAG
  changes, and ROM-log eFuse changes disabled. Its configure-time safety gate
  rejects a stale or edited `sdkconfig` that enables any known irreversible
  startup setting.
- `encrypted-production`: full release-mode encryption and permanent UART ROM
  download disable on first hardware boot. Do not flash this profile during
  bring-up. Flash encryption necessarily burns its key, enable bits, JTAG
  disable, direct-boot disable, and related security eFuses; this profile is
  compiled only for Phase 2 and belongs to the later audited provisioning
  workflow.

There is deliberately no encrypted development profile. ESP32-P4 hardware
flash encryption requires physical eFuse programming, so it cannot meet the
development requirement that every MCU change remain reversible.

The `boot-a`, `boot-b`, and `main` build variants prove that the custom loader
can select each fixed role. The override validates all exact label, type,
subtype, offset, and size tuples; rejects `otadata` and unexpected app
partitions; and gives the stock ESP-IDF handoff routine an isolated state that
contains only the chosen partition. Thus a bad target cannot invoke stock
fallback to a different image. Deep-sleep validation skipping, generic OTA
rollback, factory-reset selection, test-app selection, and TEE loading are off.
The generated `flash`, `app-flash`, `flash_args`, and `flasher_args.json`
artifacts place the validation application in the selected role: `boot_a` at
`0x20000`, `boot_b` at `0x120000`, or `main` at `0x220000`.

Run all local compile/size checks with:

```sh
platforms/esp32-p4-wifi6-touch-lcd/tools/validate.sh
```

The validated host toolchain is ESP-IDF v5.5.5 with
`riscv32-esp-elf-gcc 14.2.0`. Plaintext `boot-a`, `boot-b`, and `main`
variants build, as does the compile-only encrypted production `boot-a`
variant. The validation application binaries are `0x2e510` bytes plaintext and
`0x2fdb0` bytes encrypted. The custom Root Loader is `0x59c0` bytes plaintext
for `boot_a` (`0x59d0` for the OTA-role variants) and `0x8a30` bytes encrypted.
Run `tools/diff-root-loader.sh` to review the complete delta from the pinned
stock source. `esptool image-info` identifies the app as ESP32-P4, 16 MiB
flash, 64 KiB MMU pages, and reports both its checksum and appended SHA-256
validation hash as valid.

## Provisional layout

Phase 2 uses 16 MiB as the minimum supported physical flash size and as the
portable logical address window. ESP-IDF rejects a chip smaller than the image
header but accepts a larger chip; on larger modules this image deliberately
uses only the first 16 MiB and leaves the remaining capacity untouched. Header
auto-detection is disabled so flashing a larger unit cannot produce a
unit-specific artifact. The validation application reports both ESP-IDF's
configured size and `esp_flash_get_physical_size()` so 16, 32, and 64 MiB
modules can be distinguished during hardware validation.

`partition_layout.cmake` is the single source for the Root Loader and Specter
Bootloader partition sizes. `build.sh` derives the `boot_a`, `boot_b`, and
`main` offsets from those constants and generates a build-local partition CSV;
the Root Loader allow-list is compiled from the same derived values.

| Role | Type/subtype | Offset | Partition size | Phase 2 max image |
| --- | --- | ---: | ---: | ---: |
| `boot_a` | `app,factory` | `0x020000` | 1 MiB | 1 MiB - 4 KiB |
| `boot_b` | `app,ota_0` | `0x120000` | 1 MiB | 1 MiB - 4 KiB |
| `main` | `app,ota_1` | `0x220000` | 4 MiB | 4 MiB - 4 KiB |
| `main_aux` | `data,0x40` | `0x620000` | 4 MiB | unused |

The last 4 KiB of each app partition is reserved for the later approval/trial
trailer. `main_aux` is encrypted when encryption is enabled, is not an app
partition, and is absent from the Root Loader allow-list.

ESP32-P4 fixes the Root Loader offset at `0x2000`. The partition table is moved
to `0x10000`: the stock encryption path makes the Phase 2 Root Loader `0x8a70`
bytes, which cannot fit before the default `0x8000` table. The new offset leaves
56 KiB for the second stage and keeps the first app 64 KiB-aligned at `0x20000`.
Every build checks the generated bootloader against this limit.

## Validation matrix

The repeatable commands are:

```sh
# Both vendor display examples (copies to a temporary tree before resolving components)
platforms/esp32-p4-wifi6-touch-lcd/tools/validate-waveshare.sh

# Disposable upstream TEE sizing experiment; TEE remains out of the product
platforms/esp32-p4-wifi6-touch-lcd/tools/validate-tee-sizing.sh

# QEMU is invoked from the pinned IDF wrapper after a plaintext build
platforms/esp32-p4-wifi6-touch-lcd/tools/idf.sh \
  -C platforms/esp32-p4-wifi6-touch-lcd \
  -B build/esp32-p4-wifi6-touch-lcd/plaintext-dev-boot-a qemu monitor
```

The pinned ESP-IDF rejects that command with `QEMU is not supported for target
esp32p4`. Consequently no CPU-startup smoke test is available for ESP32-P4 in
v5.5.5. If upstream adds support, QEMU will still validate only CPU startup,
the partition table, and serial boot flow—not Waveshare MIPI DSI, GT911,
backlight, SDMMC, USB wiring, real flash capacity, PSRAM signal integrity, or
irreversible eFuse behavior.

Both vendor `07_Displaycolorbar` examples build unchanged with ESP-IDF v5.5.5.
The validator copies each archived example to a temporary tree, gives it a
fresh build directory and generated `sdkconfig`, and resolves its declared
dependencies without editing `CMakeLists.txt` or injecting a registry `usb`
component. The 4.3-inch application binary is `0x48210` bytes and the 5-inch
binary is `0x3fca0` bytes. The component manager resolved these common versions
for both boards:

| Component | Version |
| --- | --- |
| `espressif/button` | 4.2.0 |
| `espressif/cmake_utilities` | 0.5.3 |
| `espressif/esp_codec_dev` | 1.2.0 |
| `espressif/esp_lcd_touch` | 1.2.1 |
| `espressif/esp_lcd_touch_gt911` | 1.2.0~2 |
| `espressif/esp_lv_decoder` | 0.4.3 |
| `espressif/esp_lv_fs` | 1.0.1 |
| `espressif/esp_lvgl_adapter` | 0.1.4 |
| `espressif/esp_mmap_assets` | 2.0.0 |
| `espressif/esp_new_jpeg` | 1.0.2 |
| `espressif/freetype` | 2.14.2 |
| `espressif/knob` | 1.1.0 |
| `espressif/libpng` | 1.6.58 |
| `espressif/zlib` | 1.3.2 |
| `lvgl/lvgl` | 9.4.0 |

The 4.3-inch example additionally resolves `esp_lcd_st7701` 2.0.2~2. The
5-inch example additionally resolves `i2c_bus` 1.5.2 and includes its local
`esp_lcd_hx8394` 1.0.3 component. Exact hashes and direct dependency sets are
written to the per-board lock files under the run-log directory.

The disposable TEE command is also deliberately retained as a capability
probe. ESP-IDF v5.5.5 reports that ESP-TEE supports only ESP32-C6, H2, and C5,
then rejects the P4 build because `esp_tee` is not registered for that target.
TEE image sizes, MMU alignment, partition requirements, and eFuse key-block
needs therefore cannot be measured for ESP32-P4 with the pinned release; no
TEE partitions or code are included in Phase 2.

Hardware measurements are currently blocked because neither Waveshare board
appears as a serial port on the validation host.
Never run flash, erase, or eFuse commands merely to complete this phase. Record
the board SKU, flash JEDEC ID/capacity, configured and physical flash-size
results, PSRAM size, chip revision, and tool versions when hardware is
deliberately attached.
