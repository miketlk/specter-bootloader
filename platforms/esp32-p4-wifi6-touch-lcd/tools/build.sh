#!/bin/sh
set -eu

platform_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
repo_root=$(CDPATH= cd -- "$platform_dir/../.." && pwd -P)
profile=${1:-plaintext-dev}
target=${2:-boot-a}
board=${SPECTER_BOARD:-4p3}
keys=${SPECTER_KEYS:-test}
app=${SPECTER_APP:-bootloader}
mock_bloat=${SPECTER_MOCK_BLOAT_BYTES:-0}
mock_version=${SPECTER_MOCK_VERSION:-1.0.0}

# Only mock-main resolves the exactly pinned official CBOR component. The
# bootloader remains a dependency-manager-free build.
if [ "$app" = bootloader ]; then
  export IDF_COMPONENT_MANAGER=0
fi
allow_irreversible_config=OFF
allow_irreversible_hardware=${SPECTER_ALLOW_IRREVERSIBLE_HARDWARE:-0}

if [ "$#" -ge 1 ]; then shift; fi
if [ "$#" -ge 1 ]; then shift; fi

for arg in "$@"; do
  case "$arg" in
    *SPECTER_ALLOW_IRREVERSIBLE_CONFIG*)
      echo "error: the profile controls irreversible-configuration policy" >&2
      exit 2
      ;;
  esac
done

case "$allow_irreversible_hardware" in
  0|1) ;;
  *)
    echo "error: SPECTER_ALLOW_IRREVERSIBLE_HARDWARE must be 0 or 1" >&2
    exit 2
    ;;
esac

case "$profile" in
  plaintext-dev)
    flash_defaults="$platform_dir/sdkconfig.defaults.flash-plaintext"
    lockdown_defaults="$platform_dir/sdkconfig.defaults.lockdown.dev"
    require_reversible_config=ON
    ;;
  encrypted-production)
    flash_defaults="$platform_dir/sdkconfig.defaults.flash-encrypted"
    lockdown_defaults="$platform_dir/sdkconfig.defaults.lockdown.production"
    allow_irreversible_config=ON
    ;;
  *)
    echo "usage: $0 {plaintext-dev|encrypted-production} {boot-a|boot-b|main} [idf.py arguments]" >&2
    exit 2
    ;;
esac

case "$target" in
  boot-a|boot-b|main) ;;
  *)
    echo "usage: $0 {plaintext-dev|encrypted-production} {boot-a|boot-b|main} [idf.py arguments]" >&2
    exit 2
    ;;
esac

case "$app:$profile:$target" in
  bootloader:*:boot-a|bootloader:*:boot-b|mock-main:plaintext-dev:main) ;;
  bootloader:*:main)
    echo "error: Specter Bootloader cannot be selected for the main role" >&2
    exit 2
    ;;
  mock-main:*:*)
    echo "error: mock-main is allowed only for plaintext-dev:main" >&2
    exit 2
    ;;
  *)
    echo "error: SPECTER_APP must be bootloader or mock-main" >&2
    exit 2
    ;;
esac

if [ "$profile" = encrypted-production ] &&
    [ "$allow_irreversible_hardware" != 1 ]; then
  for arg in "$@"; do
    case "$arg" in
      flash|*-flash)
        echo "error: encrypted-production flashing requires SPECTER_ALLOW_IRREVERSIBLE_HARDWARE=1" >&2
        exit 2
        ;;
    esac
  done
fi

case "$mock_bloat" in
  ''|*[!0-9]*)
    echo "error: SPECTER_MOCK_BLOAT_BYTES must be an unsigned base-10 integer" >&2
    exit 2
    ;;
esac
mock_version_major=${mock_version%%.*}
mock_version_remainder=${mock_version#*.}
mock_version_minor=${mock_version_remainder%%.*}
mock_version_patch=${mock_version_remainder#*.}
case "$mock_version_major:$mock_version_minor:$mock_version_patch" in
  *[!0-9:]*|*:*:*:*)
    echo "error: SPECTER_MOCK_VERSION must be major.minor.patch" >&2
    exit 2
    ;;
esac
if [ -z "$mock_version_major" ] || [ -z "$mock_version_minor" ] ||
    [ -z "$mock_version_patch" ] ||
    [ "$mock_version_major" -gt 41 ] 2>/dev/null ||
    [ "$mock_version_minor" -gt 999 ] 2>/dev/null ||
    [ "$mock_version_patch" -gt 999 ] 2>/dev/null; then
  echo "error: SPECTER_MOCK_VERSION components exceed tag10 bounds" >&2
  exit 2
fi
if [ "$mock_bloat" -gt 4294967295 ] 2>/dev/null; then
  echo "error: SPECTER_MOCK_BLOAT_BYTES exceeds uint32_t" >&2
  exit 2
fi
if [ "$app" != mock-main ] && [ "$mock_bloat" != 0 ]; then
  echo "error: bloat is valid only for mock-main" >&2
  exit 2
fi

case "$board" in
  4p3|5) ;;
  *)
    echo "error: SPECTER_BOARD must be 4p3 or 5" >&2
    exit 2
    ;;
esac
export SPECTER_BOARD="$board"

if [ "$app" = mock-main ]; then
  build_dir="$repo_root/build/esp32-p4-wifi6-touch-lcd/mock-$profile-$board-$target-bloat-$mock_bloat"
elif [ "$board" = 4p3 ]; then
  # Preserve the Phase 2 build-directory ABI used by validation evidence.
  build_dir="$repo_root/build/esp32-p4-wifi6-touch-lcd/$profile-$target"
else
  build_dir="$repo_root/build/esp32-p4-wifi6-touch-lcd/$profile-$board-$target"
fi

if [ "$board" = 4p3 ]; then
  board_profile=lcd-4p3
else
  board_profile=lcd-5
fi
layout_dir="$build_dir/partition-layout"

cmake \
  -D "SPECTER_PLATFORM_DIR=$platform_dir" \
  -D "SPECTER_LAYOUT_OUTPUT_DIR=$layout_dir" \
  -P "$platform_dir/tools/generate-partition-layout.cmake"

defaults="$platform_dir/sdkconfig.defaults;$platform_dir/sdkconfig.defaults.board-$board;$flash_defaults;$lockdown_defaults;$platform_dir/sdkconfig.defaults.root-$target;$layout_dir/sdkconfig.defaults"

exec "$platform_dir/tools/idf.sh" \
  -C "$platform_dir" \
  -B "$build_dir" \
  -D "SDKCONFIG=$layout_dir/sdkconfig" \
  -D "SDKCONFIG_DEFAULTS=$defaults" \
  -D "SPECTER_ALLOW_IRREVERSIBLE_CONFIG=$allow_irreversible_config" \
  -D "SPECTER_KEYS=$keys" \
  -D "SPECTER_APP=$app" \
  -D "SPECTER_BOARD_PROFILE=$board_profile" \
  -D "SPECTER_MOCK_BLOAT_BYTES=$mock_bloat" \
  -D "SPECTER_MOCK_VERSION=$mock_version" \
  "$@"
