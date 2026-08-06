#!/bin/sh
set -eu

# This project has no managed components. Avoid the component manager's psutil
# process-tree lookup, which is unnecessary and blocked by macOS sandboxes.
export IDF_COMPONENT_MANAGER=0

platform_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
repo_root=$(CDPATH= cd -- "$platform_dir/../.." && pwd -P)
profile=${1:-plaintext-dev}
target=${2:-boot-a}
board=${SPECTER_BOARD:-4p3}
keys=${SPECTER_KEYS:-test}
allow_irreversible_config=OFF

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

case "$board" in
  4p3|5) ;;
  *)
    echo "error: SPECTER_BOARD must be 4p3 or 5" >&2
    exit 2
    ;;
esac

if [ "$board" = 4p3 ]; then
  # Preserve the Phase 2 build-directory ABI used by validation evidence.
  build_dir="$repo_root/build/esp32-p4-wifi6-touch-lcd/$profile-$target"
else
  build_dir="$repo_root/build/esp32-p4-wifi6-touch-lcd/$profile-$board-$target"
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
  "$@"
