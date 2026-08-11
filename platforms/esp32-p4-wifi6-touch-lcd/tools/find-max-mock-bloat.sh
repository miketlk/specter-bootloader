#!/bin/sh
set -eu

platform_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
board=${1:-4p3}

case "$board" in
  4p3|5) ;;
  *)
    echo "usage: $0 {4p3|5}" >&2
    exit 2
    ;;
esac

layout_dir=$(mktemp -d "${TMPDIR:-/tmp}/specter-mock-layout.XXXXXX")
trap 'cmake -E remove_directory "$layout_dir"' EXIT HUP INT TERM
cmake -D "SPECTER_PLATFORM_DIR=$platform_dir" \
  -D "SPECTER_LAYOUT_OUTPUT_DIR=$layout_dir" \
  -P "$platform_dir/tools/generate-partition-layout.cmake"
main_size=$(awk -F= '$1 == "main_partition_size" {print $2}' \
  "$layout_dir/layout.metadata")
trailer_size=$(awk -F= '$1 == "trailer_size" {print $2}' \
  "$layout_dir/layout.metadata")

# The authoritative post-build checker decides whether a candidate fits. A
# bounded binary search accounts for ESP image segment and footer alignment.
low=0
high=$((main_size - trailer_size))
while [ "$low" -lt "$high" ]; do
  candidate=$(((low + high + 1) / 2))
  if SPECTER_BOARD="$board" SPECTER_APP=mock-main \
      SPECTER_MOCK_BLOAT_BYTES="$candidate" \
      "$platform_dir/tools/build.sh" plaintext-dev main build >/dev/null 2>&1; then
    low=$candidate
  else
    high=$((candidate - 1))
  fi
done

SPECTER_BOARD="$board" SPECTER_APP=mock-main SPECTER_MOCK_BLOAT_BYTES="$low" \
  "$platform_dir/tools/build.sh" plaintext-dev main build size
echo "largest fitting mock filler for $board: $low bytes"
