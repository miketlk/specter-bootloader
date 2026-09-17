import pytest
from flash_inspect.boot_policy import analyze


def candidate(role, version, sequence, state, approved="valid", capacity=1):
    return {
        "id": f"boot_{role}",
        "role": role,
        "approval": {
            "state": approved,
            "version": {"raw": version},
            "sequence": sequence,
        },
        "journal": {"effective_state": state, "free_capacity": capacity},
    }


@pytest.mark.parametrize(
    "av,bv,aseq,bseq,astate,bstate,expected",
    [
        (1, 2, 1, 2, "confirmed", "none", "boot_2"),
        (1, 2, 1, 2, "confirmed", "attempted", "boot_1"),
        (2, 1, 1, 2, "confirmed", "none", "boot_1"),
        (2, 2, 1, 2, "confirmed", "none", "boot_1"),
        (2, 2, 1, 2, "confirmed", "confirmed", "boot_2"),
        (2, 2, 2, 2, "confirmed", "confirmed", "boot_1"),
        (2, 2, 2, 2, "none", "none", "boot_1"),
        (1, 2, 1, 2, "attempted", "attempted", None),
    ],
)
def test_selection(av, bv, aseq, bseq, astate, bstate, expected):
    regions = [
        candidate(1, av, aseq, astate),
        candidate(2, bv, bseq, bstate),
        candidate(3, 100, 10, "none"),
    ]
    report = analyze(regions, {"checks": {"root_policy": {"status": "pass"}}})
    assert report["selection"] == expected
    assert report["main"]["requires_matching_rtc_request"]


def test_full_newer_trial_uses_confirmed_fallback():
    report = analyze(
        [
            candidate(1, 1, 1, "confirmed"),
            candidate(2, 2, 2, "none", capacity=0),
            candidate(3, 1, 1, "none"),
        ],
        {"checks": {"root_policy": {"status": "pass"}}},
    )
    assert report["state"] == "conditional_selection"
    assert report["selection"] == "boot_1"
    assert report["candidates"][1]["disqualification_codes"] == [
        "TRIAL_MARKER_CAPACITY_UNAVAILABLE"
    ]


@pytest.mark.parametrize("status", ["unknown", "fail"])
def test_unknown_layout(status):
    assert (
        analyze([], {"checks": {"root_policy": {"status": status}}})["selection"]
        is None
    )
