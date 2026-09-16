# ESP32-P4 SD uploader

A standalone development application that runs entirely from internal SRAM and
writes only the removable microSD card. It is separate from Specter Bootloader
and Mock Main Firmware. It does not approve or install firmware.

The transport is the CH343 **USB TO UART** connector, defaulting to 4000000 baud.
Leave the USB OTG cable connected if needed for the fixture; this application
currently provides no CDC endpoint. Supported build profiles are `lcd-4p3` and
`lcd-5`, for ESP32-P4 revisions 1.0–1.99. Later silicon requires a separate RAM
layout qualification. Neither cameras nor the ESP32-C6 are initialized.

From the repository root:

```sh
. ./third_party/esp-idf/export.sh
python -m pip install --require-hashes -r tools/requirements-sd-uploader.txt
platforms/esp32-p4-wifi6-touch-lcd/sd_uploader/build.sh lcd-4p3
platforms/esp32-p4-wifi6-touch-lcd/sd_uploader/build.sh lcd-5
# Explicit profiles: 115200, 460800, 921600, 2000000, 3000000, 4000000 baud
platforms/esp32-p4-wifi6-touch-lcd/sd_uploader/build.sh lcd-4p3 4000000
```

Each build writes its ELF, executable image and `ram-manifest.json` under
`build/esp32-p4-wifi6-touch-lcd/sd-uploader/<board>/uart/` for the default rate,
or `uart-<baud>/` for other rates. If an older default build retains another baud rate in
its generated `sdkconfig`, remove that build directory before rebuilding at the
new default; a stale configuration is rejected. Ordinary host commands must use the matching
`--baud`; the runner reads it from the audited manifest. ROM loading negotiates
921600 baud and switches the same descriptor to the application rate afterward.
The mandatory audit
checks ELF/image byte agreement, internal SRAM residency, entry point, BSS,
reserved regions, forbidden flash/eFuse functions, configuration and runtime RAM
budget. Host loading repeats this audit. No second-stage bootloader or partition
table is built. Flash, erase and merge/deployment targets fail intentionally;
ignore ESP-IDF's generic post-build flashing suggestions.

The local SD-only FatFs component references licensed sources from the pinned
IDF without modifying them. Writable file access is private to this app. Mount
failures never format the card. A transaction writes a reserved `.part` file,
checks flush/sync/close, hashes it independently, renames without overwriting,
and hashes the published file again. `RELEASE` unmounts and deinitializes SD;
it does not reset the chip. Failed/uncertain transactions prevent release.

Pure-RAM startup explicitly initializes the pinned cache HAL before heap setup,
then clears high BSS with the configured SRAM/cache split active. Initialized
startup tables precede the expandable buffers. This is required for reliable
SD DMA on P4; omitting it can corrupt readback despite apparently valid images.
The parser accepts bounded chunks up to 16 KiB and exposes device timing counters.
Aligned 16 KiB file buffers and the pinned driver's bounded multi-sector path
avoid single-sector I/O without changing flush or independent verification rules.

On the qualified lcd-4p3/CH343/macOS fixture, a 4,190,208-byte upload including both
commit readbacks took 29.6 seconds at 2 Mbaud, 21.7 seconds at 3 Mbaud,
and 18.3–18.7 seconds at the default 4 Mbaud, using 16 KiB chunks. These figures exclude RAM
loading and NOR comparison scans. All three rates passed without natural retries.
A 5 Mbaud experiment failed the initial handshake and is not a supported profile.

See [the host tools guide](../../../tools/README.md#esp32-p4-sd-uploader) for the
CLI, fixture declarations, lifecycle, recovery and tests. CDC and raw sector
operations are intentionally outside this UART implementation.
