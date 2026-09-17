"""Shared machine-readable check vocabulary."""


def check(value, code, **evidence):
    return {
        "status": "unknown" if value is None else "pass" if value else "fail",
        "code": None if value is True else code,
        **evidence,
    }


def all_pass(checks):
    return all(c["status"] == "pass" for c in checks.values())


def cstring(raw):
    try:
        end = raw.index(0)
        return {"value": raw[:end].decode("utf-8"), "valid": True}
    except (ValueError, UnicodeDecodeError):
        return {"value": None, "valid": False, "raw_hex": raw.hex()}
