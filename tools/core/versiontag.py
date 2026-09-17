"""Dependency-free validation of the fixed-width embedded version tag."""

VERSION_TAG = b"<version:tag10>"
VERSION_TAG_CLOSE = b"</version:tag10>"
VERSION_DIGITS = 10


def read_version_tag(data, offset):
    """Read a tag at offset; reject incomplete digits or closing delimiters."""
    start = offset + len(VERSION_TAG)
    end = start + VERSION_DIGITS
    digits = data[start:end]
    if (
        data[offset:start] != VERSION_TAG
        or len(digits) != VERSION_DIGITS
        or not digits.isdigit()
        or data[end : end + len(VERSION_TAG_CLOSE)] != VERSION_TAG_CLOSE
    ):
        raise ValueError("Corrupted version tag in payload")
    return int(digits)
