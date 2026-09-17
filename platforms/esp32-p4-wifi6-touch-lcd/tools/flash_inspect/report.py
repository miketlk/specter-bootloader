"""Deterministic report assembly and strict mandatory-evidence assessment."""

import hashlib
from importlib.metadata import PackageNotFoundError, version
from . import __version__, abi, approvals, boot_policy, images, journal, layout, nvs
from .backends import ROOT, IDF
from .checks import check


def empty_report():
    return {
        "schema_version": 1,
        "tool": {
            "version": __version__,
            "abi_revision": 2,
            "policy_revision": "root-normal-reset-v2",
            "python_floor": "3.10",
            "backend_provenance": {},
        },
        "status": "error",
        "input": {},
        "expectations": {},
        "coverage": {},
        "partition_table": {},
        "regions": [],
        "boot_analysis": {},
        "findings": [],
        "summary": {"validation": "incomplete", "finding_count": 0},
    }


def finding(report, code, region=None, offset=None, severity="error", details=None):
    report["findings"].append(
        {
            "code": code,
            "severity": severity,
            "region_id": region,
            "offset": offset,
            "message": code.replace("_", " ").lower(),
            "details": details or {},
        }
    )


def collect(report, checks, region, offset):
    for name, c in checks.items():
        if c["status"] in ("fail", "unknown", "unsupported"):
            finding(
                report,
                c["code"],
                region,
                offset,
                "error" if c["status"] == "fail" else "warning",
                {"check": name, **c},
            )


def region(space, id_, label, kind, offset, size, source, role=None):
    return {
        "id": id_,
        "label": label,
        "kind": kind,
        "role": role,
        "offset": offset,
        "size": size,
        "geometry_source": source,
        "coverage": space.coverage(offset, size),
        "population": space.population(offset, size),
        "image": None,
        "approval": None,
        "journal": None,
        "nvs": None,
        "checks": {},
    }


def inspect(space, fmt, data, metadata, args, expected_entries, layout_digest):
    result = empty_report()
    try:
        result["tool"]["esptool_version"] = version("esptool")
    except PackageNotFoundError:
        result["tool"]["esptool_version"] = None
    for name, path in [
        ("partition", IDF / "components/partition_table/gen_esp32part.py"),
        ("nvs", IDF / "components/nvs_flash/nvs_partition_tool/nvs_parser.py"),
        ("image", ROOT / "tools/core/espidf.py"),
        ("abi", layout.PLATFORM / "common/esp32p4_boot_contract.h"),
        ("idf_version", IDF / "tools/cmake/version.cmake"),
    ]:
        result["tool"]["backend_provenance"][name] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else None,
        }
    result["input"] = {
        "format": fmt,
        "source_size": len(data),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "base_offset": args.base_offset or 0 if fmt in ("bin", "hex") else None,
        "address_model": "sparse_absolute",
        "ranges": space.supplied(),
        "content_mode": args.content_mode,
        **metadata,
    }
    result["expectations"] = {
        "board": args.board,
        "inferred_board": None,
        "layout_sha256": layout_digest,
        "layout_source": "override" if args.expected_layout else "repository",
        "partition_table_offset": args.partition_table_offset,
        "logical_capacity": layout.defaults()[1],
    }
    table = layout.inspect(space, args.partition_table_offset, expected_entries)
    if args.content_mode == "ciphertext":
        table = {
            "offset": args.partition_table_offset,
            "entries": [],
            "valid": False,
            "missing_expected": [],
            "backend": "idf/gen_esp32part.py",
            "checks": {
                "content": check(None, "CONTENT_UNREADABLE"),
                "root_policy": check(None, "CONTENT_UNREADABLE"),
            },
        }
    result["partition_table"] = table
    if (
        args.content_mode != "plaintext"
        and not table["entries"]
        and table["checks"].get("structure", {}).get("status") == "fail"
    ):
        for c in table["checks"].values():
            if c["status"] == "fail":
                c.update(status="unknown", code="CONTENT_UNREADABLE")
    collect(result, table["checks"], "partition_table", args.partition_table_offset)
    regions = result["regions"]
    regions.extend(
        [
            region(
                space,
                "reserved_prefix",
                "reserved_prefix",
                "reserved",
                0,
                0x2000,
                "fixed",
            ),
            region(
                space,
                "root",
                "root",
                "root",
                0x2000,
                args.partition_table_offset - 0x2000,
                "fixed",
            ),
            region(
                space,
                "partition_table",
                "partition_table",
                "partition_table",
                args.partition_table_offset,
                4096,
                "fixed",
            ),
        ]
    )
    observed = table["entries"]
    for index, p in enumerate(observed):
        role = (
            abi.ROLES.get(p["label"])
            if p["type"] == 0
            and p["subtype"] == {"boot_a": 0, "boot_b": 16, "main": 17}.get(p["label"])
            else None
        )
        valid_bounds = p["size"] > 0 and p["offset"] + p["size"] <= 2**32
        if not valid_bounds:
            continue
        r = region(
            space,
            f"partition:{index}:{p['label']}",
            p["label"],
            "partition",
            p["offset"],
            p["size"],
            "observed_table",
            role,
        )
        r["partition"] = p
        regions.append(r)
    if not table["valid"]:
        for p in expected_entries:
            # Recovery regions deliberately replace decoder roles on invalid
            # observed entries; the observed geometry remains in the inventory.
            r = region(
                space,
                "recovery:" + p["label"],
                p["label"],
                "recovery_candidate",
                p["offset"],
                p["size"],
                "expected_layout",
                abi.ROLES.get(p["label"]),
            )
            r["partition"] = p
            regions.append(r)
        for r in regions:
            if r["geometry_source"] == "observed_table":
                r["role"] = None
    # Inventory the complement of declared/recovery/fixed intervals, including
    # supplied capacity beyond the logical layout without inferring chip size.
    extent = max(layout.defaults()[1], space.end)
    cursor = 0
    gaps = []
    for r in sorted(regions, key=lambda r: r["offset"]):
        if r["offset"] > cursor:
            gaps.append((cursor, r["offset"] - cursor))
        cursor = max(cursor, r["offset"] + r["size"])
    if cursor < extent:
        gaps.append((cursor, extent - cursor))
    for start, size in gaps:
        r = region(
            space,
            f"unallocated:{start}",
            "unallocated",
            "unallocated",
            start,
            size,
            "derived",
        )
        if r["population"] == "non_erased":
            r["checks"]["population"] = check(False, "UNALLOCATED_POPULATED")
        regions.append(r)
    for r in regions:
        mode = args.content_mode
        if r["kind"] == "root" or r.get("partition", {}).get("type") == 0:
            capacity = r["size"] - (abi.TRAILER_SIZE if r["role"] else 0)
            if capacity <= 0:
                r["checks"]["size"] = check(False, "LAYOUT_MISMATCH")
                continue
            r["image"] = images.inspect(
                space, r["offset"], capacity, root=r["kind"] == "root", mode=mode
            )
            if r["role"]:
                r["approval"] = approvals.inspect(
                    space,
                    r["offset"] + capacity,
                    capacity,
                    r["role"],
                    r["image"],
                    args.board,
                    mode,
                )
        partition = r.get("partition", {})
        decoding = r["geometry_source"] != "observed_table" or table["valid"]
        if (
            decoding
            and r["label"] != "nvs_keys"
            and partition.get("type") == 1
            and partition.get("subtype") == 2
        ):
            r["nvs"] = nvs.inspect(
                space,
                r["offset"],
                r["size"],
                mode
                if partition.get("flags", 0) & 1
                else "auto"
                if mode == "ciphertext"
                else mode,
            )
    platforms = {
        r["approval"]["platform"]["value"]
        for r in regions
        if r["approval"]
        and r["approval"].get("checks", {}).get("crc", {}).get("status") == "pass"
        and r["approval"].get("checks", {}).get("platform", {}).get("status") == "pass"
    }
    inferred = next((b for b, p in abi.PLATFORMS.items() if platforms == {p}), None)
    result["expectations"]["inferred_board"] = inferred
    if args.board == "auto" and inferred:
        for r in regions:
            if r["approval"]:
                r["approval"] = approvals.inspect(
                    space,
                    r["offset"] + r["size"] - abi.TRAILER_SIZE,
                    r["size"] - abi.TRAILER_SIZE,
                    r["role"],
                    r["image"],
                    inferred,
                    args.content_mode,
                )
    if len(platforms) > 1:
        finding(result, "BOARD_CONFLICT")
    by_role = {r["role"]: r for r in regions if r["role"]}
    journals = [
        r
        for r in regions
        if r["label"] == "boot_journal"
        and (r["geometry_source"] == "expected_layout" or table["valid"])
        and r.get("partition", {}).get("type") == 1
        and r.get("partition", {}).get("subtype") == 65
        and r["size"] == 8192
    ]
    for r in journals:
        r["journal"] = {"sectors": []}
        if r["partition"]["flags"] & 1:
            r["checks"]["plaintext"] = check(False, "JOURNAL_ENCRYPTED_FLAG")
            continue
        for role in (1, 2):
            app = by_role.get(role)
            seq = (app.get("approval") or {}).get("sequence") if app else None
            sector = journal.inspect_sector(
                space, r["offset"] + (role - 1) * abi.JOURNAL_SECTOR_SIZE, role, seq
            )
            r["journal"]["sectors"].append(sector)
            if app:
                app["journal"] = sector
    missing = []
    incomplete = False
    for r in regions:
        collect(result, r["checks"], r["id"], r["offset"])
        if (
            r["kind"] in ("root", "partition_table")
            or r["role"]
            or r["label"] in ("nvs", "boot_journal")
        ):
            missing.extend(
                {"region_id": r["id"], **m}
                for m in space.missing(r["offset"], r["size"])
            )
            if r["coverage"]["status"] != "complete":
                incomplete = True
                finding(
                    result,
                    "RANGE_MISSING",
                    r["id"],
                    r["offset"],
                    "warning",
                    r["coverage"],
                )
        for field in ("image", "approval", "nvs"):
            value = r[field]
            if value:
                collect(
                    result,
                    value.get("checks", {}),
                    r["id"],
                    value.get("offset", r["offset"]),
                )
                collect(
                    result,
                    value.get("project_checks", {}),
                    r["id"],
                    value.get("offset", r["offset"]),
                )
                incomplete |= any(
                    c["status"] in ("unknown", "unsupported")
                    for c in value.get("checks", {}).values()
                )
        if r["kind"] == "root" and r["image"] and r["image"]["state"] == "erased":
            finding(result, "ROOT_IMAGE_MISSING", r["id"], r["offset"])
        if r["journal"] and "sectors" in r["journal"]:
            for sector in r["journal"]["sectors"]:
                collect(result, sector["checks"], r["id"], sector["offset"])
                for record in sector["records"]:
                    if record["validation"]["status"] != "pass":
                        finding(
                            result,
                            "JOURNAL_RECORD_INVALID",
                            r["id"],
                            record["offset"],
                            "warning",
                            {"slot": record["slot"]},
                        )
    # Project counters are consistency diagnostics, never selection rules.
    for r in regions:
        if not r["nvs"]:
            continue
        counters = r["nvs"]["values"]
        for role, app in by_role.items():
            a = app.get("approval") or {}
            if a.get("state") != "valid":
                continue
            floor = counters["floor_main" if role == 3 else "floor_boot"]
            if floor["state"] == "known" and a["version"]["raw"] < floor["value"]:
                finding(
                    result,
                    "NVS_VERSION_BELOW_INSTALL_FLOOR",
                    app["id"],
                    app["offset"],
                    severity="info",
                    details={
                        "floor": floor["value"],
                        "approval_version": a["version"]["raw"],
                        "affects_root_selection": False,
                    },
                )
            seq = counters["approval_seq"]
            if seq["state"] == "known" and a["sequence"] > seq["value"]:
                finding(result, "NVS_SEQUENCE_CONFLICT", app["id"], app["offset"])
    result["boot_analysis"] = boot_policy.analyze(regions, table)
    if result["boot_analysis"]["state"] == "no_loadable_candidate":
        finding(result, "NO_HEALTHY_BOOTLOADER")
    incomplete |= result["boot_analysis"]["state"] == "unknown"
    incomplete |= not any(r["nvs"] for r in regions)
    result["coverage"] = {
        "ranges": space.supplied(),
        "missing_expected_ranges": missing,
        "observed_extent": {
            "start": space.ranges[0][0] if space.ranges else None,
            "end": space.end,
        },
    }
    result["regions"].sort(key=lambda r: (r["offset"], r["id"]))
    result["findings"].sort(
        key=lambda f: (
            f["offset"] if f["offset"] is not None else -1,
            f["code"],
            f["region_id"] or "",
        )
    )
    failed = any(f["severity"] == "error" for f in result["findings"])
    result["summary"] = {
        "validation": "fail" if failed else "incomplete" if incomplete else "pass",
        "finding_count": len(result["findings"]),
        "region_count": len(regions),
        "error_count": sum(f["severity"] == "error" for f in result["findings"]),
    }
    result["status"] = "findings" if failed else "partial" if incomplete else "ok"
    return result
