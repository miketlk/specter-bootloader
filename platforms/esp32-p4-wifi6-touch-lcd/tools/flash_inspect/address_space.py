"""Checked sparse flash ranges; missing bytes are never synthesized."""

from bisect import bisect_right
from itertools import islice


class MissingRange(ValueError):
    pass


class AddressSpace:
    def __init__(self, ranges, max_span=64 * 1024 * 1024):
        self.ranges = []
        for start, data in sorted(ranges):
            if start < 0 or start + len(data) > 2**32:
                raise ValueError("address outside 32-bit flash space")
            if not data:
                continue
            if self.ranges and start < self.ranges[-1][0] + len(self.ranges[-1][1]):
                raise ValueError("overlapping input ranges")
            self.ranges.append((start, bytes(data)))
        if self.ranges and self.end - self.ranges[0][0] > max_span:
            raise ValueError("represented address span exceeds --max-span")
        self.starts = [a for a, _ in self.ranges]

    @property
    def end(self):
        return self.ranges[-1][0] + len(self.ranges[-1][1]) if self.ranges else 0

    def iterate(self, start, size):
        if start < 0 or size < 0 or start + size > 2**32:
            raise ValueError("invalid range")
        i = max(0, bisect_right(self.starts, start) - 1)
        for a, data in islice(self.ranges, i, None):
            if a >= start + size:
                break
            lo, hi = max(a, start), min(a + len(data), start + size)
            if lo < hi:
                yield lo, data[lo - a : hi - a]

    def read_exact(self, start, size):
        chunks = list(self.iterate(start, size))
        if sum(len(d) for _, d in chunks) != size:
            raise MissingRange(f"missing bytes at {start:#x}, length {size}")
        return b"".join(d for _, d in chunks)

    def coverage(self, start, size):
        n = sum(len(d) for _, d in self.iterate(start, size))
        return {
            "status": "complete" if n == size else "partial" if n else "absent",
            "covered_bytes": n,
        }

    def population(self, start, size):
        if any(d.count(255) != len(d) for _, d in self.iterate(start, size)):
            return "non_erased"
        return (
            "erased"
            if self.coverage(start, size)["status"] == "complete"
            else "unknown"
        )

    def missing(self, start, size):
        cursor = start
        result = []
        for a, d in self.iterate(start, size):
            if a > cursor:
                result.append({"offset": cursor, "size": a - cursor})
            cursor = a + len(d)
        if cursor < start + size:
            result.append({"offset": cursor, "size": start + size - cursor})
        return result

    def supplied(self):
        result = []
        for a, d in self.ranges:
            if result and result[-1]["offset"] + result[-1]["size"] == a:
                result[-1]["size"] += len(d)
            else:
                result.append({"offset": a, "size": len(d)})
        return result
