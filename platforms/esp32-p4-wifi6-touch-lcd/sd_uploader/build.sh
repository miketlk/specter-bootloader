#!/bin/sh
set -eu
app=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo=$(CDPATH= cd -- "$app/../../.." && pwd -P)
board=${1:-lcd-4p3}
baud=${2:-4000000}
case "$baud" in
  115200|460800|921600|2000000|3000000|4000000) ;;
  *) echo 'supported UART baud: 115200, 460800, 921600, 2000000, 3000000, 4000000' >&2; exit 2 ;;
esac
case "$board" in
  lcd-4p3) suffix=4p3 ;;
  lcd-5) suffix=5 ;;
  *) echo 'supported boards: lcd-4p3, lcd-5' >&2; exit 2 ;;
esac
profile=uart
if [ "$baud" != 4000000 ]; then profile="uart-$baud"; fi
out="$repo/build/esp32-p4-wifi6-touch-lcd/sd-uploader/$board/$profile"
mkdir -p "$out"
printf 'CONFIG_SDU_UART_BAUD=%s\n' "$baud" > "$out/baud.defaults"
exec "$app/../tools/idf.sh" -C "$app" -B "$out" \
  -D "SDKCONFIG=$out/sdkconfig" -D IDF_TARGET=esp32p4 \
  -D "SDU_REQUESTED_BAUD=$baud" \
  -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.defaults.board-$suffix;$out/baud.defaults" build
