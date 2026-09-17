"""Strict text grammars and sparse input normalization."""

import re
from .address_space import AddressSpace


HEX = re.compile(r"(?:[0-9a-fA-F]{2}\s*)*")


def records(text, limit):
    """Yield splitlines-compatible ASCII records without allocating a line list."""
    start, count = 0, 0
    for match in re.finditer(r"\r\n|[\n\r\v\f\x1c-\x1e]", text):
        count += 1
        if count > limit:
            raise ValueError("input record limit exceeds --max-input-ranges")
        yield text[start : match.start()]
        start = match.end()
    if start < len(text):
        if count >= limit:
            raise ValueError("input record limit exceeds --max-input-ranges")
        yield text[start:]


def intelhex(text, max_ranges=262144):
    ranges, starts = [], []
    base, eof = 0, False
    for line in records(text, max_ranges):
        if not line.strip():
            continue
        if eof or not re.fullmatch(r":[0-9a-fA-F]+", line):
            raise ValueError("invalid Intel HEX record or data after EOF")
        record = bytes.fromhex(line[1:])
        if len(record) < 5 or len(record) != record[0] + 5 or sum(record) & 255:
            raise ValueError("Intel HEX length/checksum mismatch")
        n, address, kind = record[0], int.from_bytes(record[1:3], "big"), record[3]
        data = record[4:-1]
        if kind == 0:
            if address + n > 65536:
                raise ValueError("Intel HEX record crosses address window")
            ranges.append((base + address, data))
        elif kind == 1 and n == 0 and address == 0:
            eof = True
        elif kind in (2, 4) and n == 2 and address == 0:
            base = int.from_bytes(data, "big") << (4 if kind == 2 else 16)
        elif kind in (3, 5) and n == 4 and address == 0:
            starts.append({"type": kind, "value": int.from_bytes(data, "big")})
        else:
            raise ValueError("unsupported or malformed Intel HEX record")
    if not eof:
        raise ValueError("Intel HEX EOF missing")
    return ranges, starts


def xxd(text, max_ranges=262144):
    ranges = []
    width = None
    short = False
    for line in records(text, max_ranges):
        match = re.fullmatch(r"([0-9a-fA-F]{8,}): (.*)", line)
        if not match or short:
            raise ValueError("invalid xxd line or data after short final line")
        # Ordinary xxd separates its byte groups and ASCII with two spaces.
        columns = re.split(r" {2,}", match[2], maxsplit=1)
        field = columns[0]
        groups = field.split(" ")
        if not groups or any(
            not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", g) for g in groups
        ):
            raise ValueError("invalid xxd byte group")
        data = bytes.fromhex("".join(groups))
        if len(columns) == 2:
            suffix = match[2][len(field) :]
            if len(suffix) < len(data) + 2 or suffix[: -len(data)].strip(" "):
                raise ValueError("xxd ASCII length mismatch")
        if any(len(g) != len(groups[0]) for g in groups[:-1]) or len(groups[-1]) > len(
            groups[0]
        ):
            raise ValueError("inconsistent xxd grouping")
        if len(data) > 256:
            raise ValueError("xxd line too wide")
        if width is None:
            width = len(data)
        elif len(data) > width:
            raise ValueError("inconsistent xxd width")
        short = len(data) < width
        ranges.append((int(match[1], 16), data))
    if not ranges:
        raise ValueError("empty xxd input")
    return ranges


def hexdump(text, max_span, max_ranges=262144):
    ranges, repeat, final = [], False, False
    represented = 0
    for line in records(text, max_ranges):
        if final:
            raise ValueError("data after hexdump final offset")
        if line == "*":
            if repeat or not ranges or len(ranges[-1][1]) != 16:
                raise ValueError("ambiguous hexdump repetition")
            repeat = True
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{8,})(?:  (.*?)  \|(.{0,16})\|)?", line)
        if not match:
            raise ValueError("invalid hexdump -C line")
        address = int(match[1], 16)
        if repeat:
            previous, data = ranges[-1]
            count = address - previous - 16
            if count <= 0 or count % 16 or represented + count > max_span:
                raise ValueError("invalid hexdump repetition extent")
            ranges.append((previous + 16, data * (count // 16)))
            represented += count
            repeat = False
        if match[2] is None:
            if not ranges or address != ranges[-1][0] + len(ranges[-1][1]):
                raise ValueError("hexdump final offset mismatch")
            final = True
        else:
            tokens = match[2].split()
            if not 1 <= len(tokens) <= 16 or any(
                not re.fullmatch("[0-9a-fA-F]{2}", t) for t in tokens
            ):
                raise ValueError("invalid hexdump bytes")
            if len(match[3]) != len(tokens):
                raise ValueError("hexdump ASCII length mismatch")
            if ranges and len(ranges[-1][1]) % 16:
                raise ValueError("data after short hexdump line")
            ranges.append((address, bytes.fromhex("".join(tokens))))
            represented += len(tokens)
            if represented > max_span:
                raise ValueError("represented address span exceeds --max-span")
    if not final:
        raise ValueError("hexdump final offset missing")
    return ranges


def decode(data, fmt="auto", base=None, max_span=64 * 1024 * 1024, max_ranges=262144):
    metadata = {}
    if fmt == "auto":
        probe = data.lstrip()
        if probe.startswith(b":"):
            fmt = "intelhex"
        elif re.match(rb"[0-9a-fA-F]{8,}:", probe):
            fmt = "xxd"
        elif re.match(rb"[0-9a-fA-F]{8,}  ", probe):
            fmt = "hexdump"
        elif probe and re.fullmatch(rb"[0-9a-fA-F\s]+", probe):
            fmt = "hex"
        else:
            fmt = "bin"
    if fmt in ("intelhex", "xxd", "hexdump") and base is not None:
        raise ValueError("--base-offset is invalid for addressed formats")
    if fmt == "bin":
        ranges = [(base or 0, data)]
    else:
        text = data.decode("ascii")
        if fmt == "hex":
            for _ in records(text, max_ranges):
                pass
            if not HEX.fullmatch(text.strip()):
                raise ValueError("plain hex requires complete byte pairs")
            ranges = [(base or 0, bytes.fromhex(text))]
        elif fmt == "intelhex":
            ranges, metadata["start_records"] = intelhex(text, max_ranges)
        elif fmt == "xxd":
            ranges = xxd(text, max_ranges)
        elif fmt == "hexdump":
            ranges = hexdump(text, max_span, max_ranges)
        else:
            raise ValueError("unsupported input format")
    return AddressSpace(ranges, max_span), fmt, metadata
