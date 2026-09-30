#!/bin/sh
set -eu

# This project uses only components from the pinned ESP-IDF checkout and this
# source tree. Disabling the component manager avoids its unnecessary psutil
# process-tree lookup, which macOS sandboxes reject because it calls sysctl.
export IDF_COMPONENT_MANAGER=0

platform_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
repo_root=$(CDPATH= cd -- "$platform_dir/../.." && pwd -P)
run_root=$(mktemp -d "${TMPDIR:-/tmp}/specter-esp32-p4-validation.XXXXXX")
echo "Validation output: $run_root"
ambient_idf=$(mktemp -d "${TMPDIR:-/tmp}/specter-ambient-idf.XXXXXX")

if IDF_PATH="$ambient_idf" "$platform_dir/tools/idf.sh" --version \
    >/dev/null 2>&1; then
  echo "error: pinned-IDF wrapper accepted an ambient external IDF_PATH" >&2
  rmdir "$ambient_idf"
  exit 1
fi
rmdir "$ambient_idf"

# Validate from clean generated state so CMake caches, compiler flags, and
# sdkconfig values from another pinned ESP-IDF version cannot affect results.
for target in boot-a boot-b main; do
  cmake -E remove_directory \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-$target"
  "$platform_dir/tools/build.sh" plaintext-dev "$target" build size
done

# Compile the irreversible production profile, but never flash or boot it here.
cmake -E remove_directory \
  "$repo_root/build/esp32-p4-wifi6-touch-lcd/encrypted-production-boot-a"
"$platform_dir/tools/build.sh" encrypted-production boot-a build size

# Every generated partition must fit the portable 16 MiB logical address
# window. ESP-IDF accepts this image on larger chips and leaves capacity above
# this window unused.
portable_flash_bytes=16777216
partition_csv="$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-boot-a/partition-layout/partitions.csv"
while IFS=, read -r name type subtype offset size flags; do
  offset=$(printf '%s' "$offset" | tr -d '[:space:]')
  size=$(printf '%s' "$size" | tr -d '[:space:]')
  case "$offset:$size" in
    0x*:0x*) ;;
    *) continue ;;
  esac
  partition_bytes=$(printf '%d' "$size")
  if [ "$partition_bytes" -eq 0 ]; then
    echo "error: zero-sized partition $name was not dropped from the generated layout" >&2
    exit 1
  fi
  partition_end=$(($(printf '%d' "$offset") + partition_bytes))
  if [ "$partition_end" -gt "$portable_flash_bytes" ]; then
    echo "error: partition $name exceeds the portable 16 MiB flash window" >&2
    exit 1
  fi
done < "$partition_csv"

layout_metadata="$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-boot-a/partition-layout/layout.metadata"
main_aux_size=$(awk -F= '$1 == "main_aux_size" {print $2}' "$layout_metadata")
if [ -z "$main_aux_size" ]; then
  echo "error: generated layout metadata does not define main_aux_size" >&2
  exit 1
fi
if [ "$(printf '%d' "$main_aux_size")" -eq 0 ]; then
  if rg -q '^[[:space:]]*main_aux[[:space:]]*,' "$partition_csv"; then
    echo "error: disabled zero-sized main_aux partition was not dropped" >&2
    exit 1
  fi
elif ! rg -q '^[[:space:]]*main_aux[[:space:]]*,' "$partition_csv"; then
  echo "error: enabled main_aux partition is missing from the generated layout" >&2
  exit 1
fi

for build in plaintext-dev-boot-a encrypted-production-boot-a; do
  image="$repo_root/build/esp32-p4-wifi6-touch-lcd/$build/specter_esp32p4_phase2_validation.bin"
  image_bytes=$(wc -c < "$image")
  if [ "$image_bytes" -gt 1044480 ]; then
    echo "error: $build image consumes the reserved 4 KiB trailer" >&2
    exit 1
  fi
done

for description in \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-boot-a/project_description.json" \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-boot-a/bootloader/project_description.json"; do
  if rg -i '"build_components".*"(esp_wifi|bt|esp_hosted|esp_coex|lwip)"' \
      "$description"; then
    echo "error: forbidden wireless/network component in Phase 2 project" >&2
    exit 1
  fi
done

if rg 'bootloader_utility_get_selected_boot_partition|bootloader_utility_load_partition_table' \
    "$platform_dir/bootloader_components/main/bootloader_start.c"; then
  echo "error: generic ESP-IDF partition selection reintroduced" >&2
  exit 1
fi

for expected in \
    'plaintext-dev-boot-a:CONFIG_SPECTER_ROOT_VALIDATION_TARGET_BOOT_A=y' \
    'plaintext-dev-boot-a:CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y' \
    'plaintext-dev-boot-a:CONFIG_PARTITION_TABLE_OFFSET=0x10000' \
    'plaintext-dev-boot-a:CONFIG_SPIRAM=y' \
    'plaintext-dev-boot-b:CONFIG_SPECTER_ROOT_VALIDATION_TARGET_BOOT_B=y' \
    'plaintext-dev-main:CONFIG_SPECTER_ROOT_VALIDATION_TARGET_MAIN=y' \
    'encrypted-production-boot-a:CONFIG_SECURE_DISABLE_ROM_DL_MODE=y'; do
  build=${expected%%:*}
  setting=${expected#*:}
  if ! rg -q "^$setting$" \
      "$repo_root/build/esp32-p4-wifi6-touch-lcd/$build/partition-layout/sdkconfig"; then
    echo "error: expected $setting in $build" >&2
    exit 1
  fi
done

for sdkconfig in \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-boot-a/partition-layout/sdkconfig" \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-boot-b/partition-layout/sdkconfig" \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-main/partition-layout/sdkconfig" \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/encrypted-production-boot-a/partition-layout/sdkconfig"; do
  if ! rg -q '^CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y$' "$sdkconfig" ||
      rg -q '^CONFIG_ESPTOOLPY_FLASHSIZE_(32MB|64MB|128MB)=y$' "$sdkconfig" ||
      ! rg -q '^# CONFIG_ESPTOOLPY_HEADER_FLASHSIZE_UPDATE is not set$' \
        "$sdkconfig"; then
    echo "error: build is not pinned to the portable 16 MiB image header" >&2
    exit 1
  fi
done

for build in plaintext-dev-boot-a plaintext-dev-boot-b plaintext-dev-main; do
  case "$build" in
    *-boot-a) partition=boot_a ;;
    *-boot-b) partition=boot_b ;;
    *-main) partition=main ;;
  esac
  partition_csv="$repo_root/build/esp32-p4-wifi6-touch-lcd/$build/partition-layout/partitions.csv"
  offset=$(awk -F, -v partition="$partition" '
    {
      name=$1
      value=$4
      gsub(/[[:space:]]/, "", name)
      gsub(/[[:space:]]/, "", value)
      if (name == partition) print value
    }' "$partition_csv")
  if [ -z "$offset" ]; then
    echo "error: cannot derive $partition offset from $partition_csv" >&2
    exit 1
  fi
  flasher_args="$repo_root/build/esp32-p4-wifi6-touch-lcd/$build/flasher_args.json"
  flash_args="$repo_root/build/esp32-p4-wifi6-touch-lcd/$build/flash_args"
  app_flash_args="$repo_root/build/esp32-p4-wifi6-touch-lcd/$build/app-flash_args"
  if ! rg -q "\"app\".*\"offset\"[[:space:]]*:[[:space:]]*\"$offset\"" \
      "$flasher_args" ||
      ! rg -q "^$offset specter_esp32p4_phase2_validation[.]bin$" \
      "$flash_args" ||
      ! rg -q "^$offset specter_esp32p4_phase2_validation[.]bin$" \
      "$app_flash_args"; then
    echo "error: $build does not flash its app at $offset" >&2
    exit 1
  fi
done

irreversible_dev_config='^CONFIG_(SECURE_FLASH_ENC_ENABLED|SECURE_BOOT|SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT|BOOTLOADER_APP_ANTI_ROLLBACK|SECURE_DISABLE_ROM_DL_MODE|SECURE_ENABLE_SECURE_ROM_DL_MODE|BOOT_ROM_LOG_ALWAYS_OFF|BOOT_ROM_LOG_ON_GPIO_HIGH|BOOT_ROM_LOG_ON_GPIO_LOW|ESP_CRYPTO_FORCE_ECC_CONSTANT_TIME_POINT_MUL|ESP_ECDSA_ENABLE_P192_CURVE)=y$'
for target in boot-a boot-b main; do
  sdkconfig="$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-$target/partition-layout/sdkconfig"
  cache="$repo_root/build/esp32-p4-wifi6-touch-lcd/plaintext-dev-$target/CMakeCache.txt"
  if ! rg -q '^SPECTER_ALLOW_IRREVERSIBLE_CONFIG:[^=]+=OFF$' "$cache"; then
    echo "error: plaintext-dev-$target bypasses the irreversible-setting gate" >&2
    exit 1
  fi
  if rg "$irreversible_dev_config" "$sdkconfig"; then
    echo "error: plaintext-dev-$target enables an irreversible MCU setting" >&2
    exit 1
  fi
  if ! rg -q '^CONFIG_BOOT_ROM_LOG_ALWAYS_ON=y$' "$sdkconfig"; then
    echo "error: plaintext-dev-$target does not preserve the ROM log eFuse" >&2
    exit 1
  fi
done

if ! rg -q '^SPECTER_ALLOW_IRREVERSIBLE_CONFIG:[^=]+=ON$' \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/encrypted-production-boot-a/CMakeCache.txt"; then
  echo "error: encrypted-production-boot-a lacks its explicit production opt-in" >&2
  exit 1
fi

if ! rg -q '^# CONFIG_SECURE_FLASH_ENCRYPT_ONLY_IMAGE_LEN_IN_APP_PART is not set$' \
    "$repo_root/build/esp32-p4-wifi6-touch-lcd/encrypted-production-boot-a/partition-layout/sdkconfig"; then
  echo "error: encrypted-production-boot-a would leave application trailer space plaintext" >&2
  exit 1
fi

"$platform_dir/tools/diff-root-loader.sh" >"$run_root/root-loader-start.diff"

echo "Phase 2 compile/size validation passed."
