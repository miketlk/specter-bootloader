"""Specter's packed decimal semantic version format."""

from functools import lru_cache
from .backends import load, ROOT

MAX_VERSION = 4199999999


def version(raw):
    valid = 0 < raw <= MAX_VERSION
    major, minor, patch, rc = (
        raw // 100000000,
        raw // 100000 % 1000,
        raw // 100 % 1000,
        raw % 100,
    )
    display = f"{major}.{minor}.{patch}" + ("" if rc == 99 else f"-rc{rc}")
    return {
        "raw": raw,
        "tag": f"{raw:010d}",
        "display": display if valid else None,
        "valid": valid,
    }


@lru_cache(maxsize=1)
def tag_backend():
    return load("flash_inspect_versiontag", ROOT / "tools/core/versiontag.py")


def tags(data, offset, limit=129):
    backend = tag_backend()
    found, cursor = [], 0
    while len(found) < limit:
        start = data.find(backend.VERSION_TAG, cursor)
        if start < 0:
            break
        try:
            detail = version(backend.read_version_tag(data, start))
        except ValueError:
            detail = {"raw": None, "tag": None, "display": None, "valid": False}
        found.append({"offset": offset + start, **detail})
        cursor = start + len(backend.VERSION_TAG)
    return found
