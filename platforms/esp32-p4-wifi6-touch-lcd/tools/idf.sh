#!/bin/sh
set -eu

platform_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
repo_root=$(CDPATH= cd -- "$platform_dir/../.." && pwd -P)
pinned_idf="$repo_root/third_party/esp-idf"

if [ ! -x "$pinned_idf/tools/idf.py" ]; then
  echo "error: initialize third_party/esp-idf recursively before building" >&2
  exit 1
fi

if [ -n "${IDF_PATH:-}" ]; then
  ambient_idf=$(CDPATH= cd -- "$IDF_PATH" 2>/dev/null && pwd -P) || {
    echo "error: ambient IDF_PATH does not exist: $IDF_PATH" >&2
    exit 1
  }
  if [ "$ambient_idf" != "$pinned_idf" ]; then
    echo "error: refusing ambient IDF_PATH=$ambient_idf" >&2
    echo "       expected pinned ESP-IDF at $pinned_idf" >&2
    exit 1
  fi
fi

gitlink=$(git -C "$repo_root" rev-parse HEAD:third_party/esp-idf)
checkout=$(git -C "$pinned_idf" rev-parse HEAD)
if [ "$gitlink" != "$checkout" ]; then
  echo "error: ESP-IDF checkout $checkout does not match gitlink $gitlink" >&2
  exit 1
fi

if [ -n "$(git -C "$pinned_idf" status --porcelain --untracked-files=no)" ]; then
  echo "error: pinned ESP-IDF checkout or one of its submodules is dirty" >&2
  exit 1
fi

# export.sh activates only tools installed for this pinned checkout.
. "$pinned_idf/export.sh" >/dev/null
exec "$pinned_idf/tools/idf.py" "$@"
