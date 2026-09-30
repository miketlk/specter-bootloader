#!/bin/sh
set -eu

platform_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
repo_root=$(CDPATH= cd -- "$platform_dir/../.." && pwd -P)
project="$repo_root/third_party/esp-idf/examples/security/tee/tee_basic"
build_dir="$repo_root/build/esp32-p4-wifi6-touch-lcd/tee-sizing"

cmake -E remove_directory "$build_dir"

exec "$platform_dir/tools/idf.sh" -C "$project" -B "$build_dir" \
  -D "IDF_TARGET=esp32p4" \
  -D "SDKCONFIG=$build_dir/sdkconfig" \
  -D "SDKCONFIG_DEFAULTS=$project/sdkconfig.defaults" \
  build size
