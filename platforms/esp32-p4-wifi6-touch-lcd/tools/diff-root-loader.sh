#!/bin/sh
set -eu

platform_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
repo_root=$(CDPATH= cd -- "$platform_dir/../.." && pwd -P)
stock="$repo_root/third_party/esp-idf/components/bootloader/subproject/main/bootloader_start.c"
override="$platform_dir/bootloader_components/main/bootloader_start.c"

# git diff exits with status 1 when differences are present. Differences are
# the expected result here, so retain useful errors while making this a review
# command that succeeds after displaying the complete local delta.
status=0
git diff --no-index -- "$stock" "$override" || status=$?
[ "$status" -eq 0 ] || [ "$status" -eq 1 ]
