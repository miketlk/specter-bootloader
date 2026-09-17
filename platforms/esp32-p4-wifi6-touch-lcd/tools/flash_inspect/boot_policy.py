"""Conditional normal-reset policy. Never identifies the running image."""


def analyze(regions, table):
    result = {
        "model_revision": "root-normal-reset-v2",
        "selection": None,
        "state": "unknown",
        "candidates": [],
        "main": None,
        "assumptions": [
            "Normal reset without a valid retained RTC request",
            "Expected geometry represents the compiled Root layout",
        ],
        "limitations": [
            "RTC state and eFuses are unavailable",
            "Hardware chip revision and health are unknown",
            "Runtime address restrictions and journal write success are not proven",
            "This is not evidence of the running slot",
        ],
    }
    by_role = {r["role"]: r for r in regions if r["role"] is not None}
    unknown = table["checks"]["root_policy"]["status"] != "pass"
    unknown |= not table.get("valid", True)
    roots = [r for r in regions if r.get("kind") == "root"]
    if roots and (roots[0].get("image") or {}).get("state") != "valid":
        unknown = True
    for role in (1, 2, 3):
        region = by_role.get(role)
        approval = region.get("approval") if region else None
        reasons = []
        state = approval["state"] if approval else "unknown"
        if state != "valid":
            reasons.append("APPROVAL_" + state.upper())
        if role != 3 and state in ("unknown", "unsupported"):
            unknown = True
        journal = (region or {}).get("journal")
        journal_state = journal["effective_state"] if journal else "unknown"
        if role != 3:
            if journal_state == "unknown" and state not in ("erased", "malformed"):
                unknown = True
                reasons.append("JOURNAL_UNKNOWN")
            elif journal_state == "attempted":
                reasons.append("TRIAL_ALREADY_ATTEMPTED")
            elif journal_state == "none":
                available = journal.get("append_available")
                if "append_available" not in journal:
                    capacity = journal.get("free_capacity")
                    available = None if capacity is None else capacity > 0
                if available is False:
                    reasons.append("TRIAL_MARKER_CAPACITY_UNAVAILABLE")
                elif available is None:
                    reasons.append("TRIAL_MARKER_CAPACITY_UNKNOWN")
        candidate = {
            "role": role,
            "region_id": region["id"] if region else None,
            "approved": state == "valid",
            "version": (approval or {}).get("version"),
            "sequence": (approval or {}).get("sequence"),
            "journal_state": journal_state,
            "disqualification_codes": reasons,
            "eligible": not reasons,
        }
        if role == 3:
            candidate["requires_matching_rtc_request"] = True
            result["main"] = candidate
        else:
            result["candidates"].append(candidate)
    if unknown:
        result["reason"] = (
            "MANDATORY_EVIDENCE_UNKNOWN"
            if table["checks"]["root_policy"]["status"] != "fail"
            else "LAYOUT_REJECTED"
        )
        return result
    candidates = result["candidates"]

    def key(c):
        return c["version"]["raw"], c["sequence"]

    confirmed = max(
        (c for c in candidates if c["eligible"] and c["journal_state"] == "confirmed"),
        key=key,
        default=None,
    )
    # Firmware chooses before appending the marker; a full newer trial does not
    # silently fall back to an older confirmed image after append failure.
    trials = [
        c
        for c in candidates
        if c["approved"]
        and c["journal_state"] == "none"
        and (confirmed is None or c["version"]["raw"] > confirmed["version"]["raw"])
    ]
    trial = max(trials, key=key, default=None)
    selected = trial or confirmed
    if (
        selected
        and "TRIAL_MARKER_CAPACITY_UNKNOWN" in selected["disqualification_codes"]
    ):
        result["reason"] = "TRIAL_MARKER_CAPACITY_UNKNOWN"
        return result
    if selected and selected["eligible"]:
        result["selection"] = selected["region_id"]
        result["state"] = "conditional_selection"
    else:
        result["state"] = "no_loadable_candidate"
        result["reason"] = (
            "TRIAL_MARKER_CAPACITY_UNAVAILABLE" if trial else "NO_HEALTHY_BOOTLOADER"
        )
    return result
