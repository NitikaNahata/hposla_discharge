"""Custom MCP server for discharge planning (AC-09).

Exposes the hospital-side capabilities the copilot needs as MCP **tools** (actions) and
**resources** (addressable read-only content). See `docs/integration-decision.md` for why this
boundary is MCP rather than a bespoke API wrapper or direct database access.

    Tools     patient_lookup · medication_interaction_check · schedule_followup
              · check_transport_availability
    Resources discharge://protocol/{condition} · formulary://medications

Run standalone (stdio transport):

    python mcp_server/discharge_server.py

The agent normally spawns this itself via `MultiServerMCPClient` — see
`src/discharge_copilot/tools/mcp_client.py`.

All backing data is SYNTHETIC and generated for this capstone. In a real deployment the bodies
of these functions would call actual hospital systems; the agent-side contract would not change.
That is the point of putting the boundary here.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from mcp.server.fastmcp import FastMCP

DATA_DIR = Path(__file__).parent / "data"

mcp = FastMCP("discharge-planning")


# ---------------------------------------------------------------------------
# Synthetic backing store
# ---------------------------------------------------------------------------


def _load(name: str) -> dict[str, Any]:
    payload = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    payload.pop("_comment", None)
    return payload


PATIENTS = _load("patients.json")
INTERACTIONS = _load("interactions.json")
PROTOCOLS = _load("protocols.json")

# Synthetic clinic availability: specialty -> weekday offsets that have open slots.
CLINIC_AVAILABILITY: dict[str, list[int]] = {
    "cardiology": [2, 4, 7, 9, 11, 14],
    "heart failure clinic": [2, 3, 5, 7, 10],
    "primary care": [1, 2, 3, 5, 6, 8, 10, 12, 14],
    "anticoagulation clinic": [1, 2, 3, 4, 6],
    "pulmonology": [4, 7, 11, 14, 18],
    "respiratory": [4, 7, 11, 14, 18],
    "endocrinology": [6, 9, 13, 17, 21],
    "surgery": [7, 10, 14, 21],
    "nephrology": [5, 9, 14, 20],
}
DEFAULT_AVAILABILITY = [3, 7, 10, 14, 21]

TRANSPORT_FLEET: dict[str, dict[str, Any]] = {
    "ambulance": {"daily_capacity": 4, "lead_time_hours": 24, "cost_band": "high"},
    "wheelchair_van": {"daily_capacity": 12, "lead_time_hours": 12, "cost_band": "medium"},
    "volunteer_driver": {"daily_capacity": 6, "lead_time_hours": 48, "cost_band": "low"},
    "taxi_voucher": {"daily_capacity": 20, "lead_time_hours": 2, "cost_band": "low"},
}


def _normalize(name: str) -> str:
    return name.strip().lower()


def _fault_delay() -> None:
    """Honour a fault-injection request from the environment (AC-12, NFR-07 evidence).

    The client sets `DISCHARGE_MCP_FAULT=timeout` when `--fault-inject mcp_timeout` is passed.
    The server then stalls past the client's timeout, producing a *genuine* tool failure for the
    self-healing loop to recover from — rather than a described-but-untriggered code path.
    """
    fault = os.getenv("DISCHARGE_MCP_FAULT", "").strip().lower()
    if fault == "timeout":
        time.sleep(float(os.getenv("DISCHARGE_MCP_FAULT_DELAY", "45")))
    elif fault == "error":
        raise RuntimeError(
            "Simulated upstream hospital system failure (DISCHARGE_MCP_FAULT=error)"
        )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def patient_lookup(mrn: str) -> dict[str, Any]:
    """Look up a patient's hospital record by medical record number.

    Returns demographics, the current admission, the active problem list, pre-admission
    medications and documented allergies. Use this to verify or enrich the case data you were
    given — particularly to confirm allergies and the pre-admission medication list before
    reconciling.

    Args:
        mrn: Medical record number, e.g. "MRN-2001".
    """
    _fault_delay()
    record = PATIENTS.get(mrn.strip().upper())
    if record is None:
        return {
            "found": False,
            "mrn": mrn,
            "error": f"No patient found with MRN '{mrn}'.",
            "available_mrns": sorted(PATIENTS.keys()),
        }
    return {"found": True, **record}


@mcp.tool()
def medication_interaction_check(medications: list[str]) -> dict[str, Any]:
    """Check a medication list for pairwise drug-drug interactions.

    Checks every unique pair against the hospital interaction reference and returns findings with
    a severity of minor, moderate, major or contraindicated, plus a management recommendation.
    Also flags high-alert medications that warrant extra scrutiny.

    Any interaction of major or contraindicated severity requires pharmacist review before the
    discharge medications are dispensed.

    Args:
        medications: Medication names, e.g. ["Warfarin", "Amiodarone", "Fluconazole"].
                     Doses may be included and are ignored.
    """
    _fault_delay()

    cleaned: list[str] = []
    for med in medications:
        # Strip dose/frequency: "Warfarin 7.5 mg once daily" -> "warfarin".
        head = _normalize(med).split(",")[0]
        tokens: list[str] = []
        for token in head.split():
            if any(ch.isdigit() for ch in token):
                break
            tokens.append(token)
        name = " ".join(tokens).strip(" .-") or head.strip()
        if name:
            cleaned.append(name)

    pairs = INTERACTIONS["pairs"]
    high_alert_ref = {_normalize(h) for h in INTERACTIONS["high_alert"]}

    findings: list[dict[str, Any]] = []
    for i, a in enumerate(cleaned):
        for b in cleaned[i + 1 :]:
            key = "|".join(sorted([a, b]))
            hit = pairs.get(key)
            if hit:
                findings.append(
                    {"drug_a": a, "drug_b": b, "checked_pair": key, **hit}
                )

    order = {"minor": 0, "moderate": 1, "major": 2, "contraindicated": 3}
    findings.sort(key=lambda f: order.get(f["severity"], 0), reverse=True)

    high_alert = sorted({m for m in cleaned if m in high_alert_ref})
    serious = [f for f in findings if f["severity"] in {"major", "contraindicated"}]

    return {
        "medications_checked": cleaned,
        "pairs_evaluated": len(cleaned) * (len(cleaned) - 1) // 2,
        "interactions_found": len(findings),
        "interactions": findings,
        "high_alert_medications": high_alert,
        "highest_severity": findings[0]["severity"] if findings else "none",
        "pharmacist_review_required": bool(serious) or bool(high_alert and findings),
        "advisory": (
            "PHARMACIST REVIEW REQUIRED before dispensing: "
            + "; ".join(f"{f['drug_a']} + {f['drug_b']} ({f['severity']})" for f in serious)
            if serious
            else "No major or contraindicated interactions detected."
        ),
    }


@mcp.tool()
def schedule_followup(
    mrn: str,
    specialty: str,
    within_days: int,
    reason: str,
    discharge_date: str = "",
) -> dict[str, Any]:
    """Book a follow-up appointment against clinic availability.

    Finds the earliest open slot in the requested specialty that falls within the requested
    window. If no slot fits, returns the next available slot instead, marked as outside the
    requested window so the plan can be adjusted or escalated.

    Args:
        mrn: Medical record number.
        specialty: Clinic, e.g. "Cardiology", "Primary Care", "Anticoagulation Clinic".
        within_days: The clinically required window, in days from discharge.
        reason: Why this follow-up is needed. Recorded on the booking.
        discharge_date: ISO date of discharge. Defaults to today.
    """
    _fault_delay()

    try:
        base = date.fromisoformat(discharge_date) if discharge_date else date.today()
    except ValueError:
        base = date.today()

    slots = CLINIC_AVAILABILITY.get(_normalize(specialty), DEFAULT_AVAILABILITY)
    within = [d for d in slots if d <= within_days]

    if within:
        offset, in_window = max(within), True
    else:
        offset, in_window = min(slots), False

    appointment_date = base + timedelta(days=offset)
    confirmation = (
        f"FU-{mrn.strip().upper().replace('MRN-', '')}-"
        f"{_normalize(specialty)[:3].upper()}-{appointment_date.strftime('%m%d')}"
    )

    return {
        "confirmed": True,
        "confirmation_id": confirmation,
        "mrn": mrn,
        "specialty": specialty,
        "scheduled_date": appointment_date.isoformat(),
        "days_from_discharge": offset,
        "requested_within_days": within_days,
        "within_requested_window": in_window,
        "reason": reason,
        "modality": "in_person",
        "note": (
            f"Booked {offset} days after discharge, within the requested {within_days}-day window."
            if in_window
            else (
                f"NO SLOT within {within_days} days. Booked the earliest available at {offset} "
                "days. If the clinical window is firm, escalate to the scheduling desk for an "
                "overbook or arrange an interim telephone review."
            )
        ),
    }


@mcp.tool()
def check_transport_availability(
    mrn: str,
    transport_type: str,
    discharge_date: str = "",
) -> dict[str, Any]:
    """Check patient-transport availability for a discharge.

    Use for patients with limited mobility, no caregiver, or no means of getting home or to
    follow-up appointments.

    Args:
        mrn: Medical record number.
        transport_type: One of "ambulance", "wheelchair_van", "volunteer_driver",
                        "taxi_voucher".
        discharge_date: ISO date of discharge. Defaults to today.
    """
    _fault_delay()

    key = _normalize(transport_type).replace(" ", "_").replace("-", "_")
    fleet = TRANSPORT_FLEET.get(key)
    if fleet is None:
        return {
            "available": False,
            "error": f"Unknown transport type '{transport_type}'.",
            "valid_types": sorted(TRANSPORT_FLEET.keys()),
        }

    try:
        when = date.fromisoformat(discharge_date) if discharge_date else date.today()
    except ValueError:
        when = date.today()

    # Deterministic synthetic utilisation, so runs are reproducible.
    booked = (when.toordinal() + len(key)) % (fleet["daily_capacity"] + 2)
    remaining = max(fleet["daily_capacity"] - booked, 0)

    return {
        "available": remaining > 0,
        "mrn": mrn,
        "transport_type": key,
        "date": when.isoformat(),
        "slots_remaining": remaining,
        "daily_capacity": fleet["daily_capacity"],
        "lead_time_hours": fleet["lead_time_hours"],
        "cost_band": fleet["cost_band"],
        "note": (
            f"{remaining} slot(s) remaining. Book at least {fleet['lead_time_hours']}h ahead."
            if remaining > 0
            else (
                f"Fully booked on {when.isoformat()}. Try taxi_voucher (2h lead time) or "
                "move the discharge to the following day."
            )
        ),
    }


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("discharge://protocol/{condition}")
def discharge_protocol(condition: str) -> str:
    """The hospital discharge protocol for a condition.

    Covers pre-discharge requirements, follow-up intervals (standard and high-risk), education
    priorities, and red-flag symptoms. Recognised conditions: heart-failure, copd, pneumonia,
    dvt-pe, post-surgical, diabetes. Common aliases resolve automatically.
    """
    # URI path segments arrive percent-encoded, so an agent asking for
    # `discharge://protocol/heart failure` sends "heart%20failure". Without decoding, every
    # multi-word alias silently misses.
    decoded = unquote(condition)
    query = _normalize(decoded).replace(" ", "-").replace("_", "-")

    protocol = PROTOCOLS.get(query)
    if protocol is None:
        for candidate in PROTOCOLS.values():
            aliases = [_normalize(a).replace(" ", "-") for a in candidate.get("aliases", [])]
            if query in aliases or any(a in query or query in a for a in aliases if a):
                protocol = candidate
                break

    if protocol is None:
        return json.dumps(
            {
                "found": False,
                "requested": decoded,
                "error": f"No discharge protocol for '{condition}'.",
                "available": sorted(PROTOCOLS.keys()),
            },
            indent=2,
        )

    return json.dumps({"found": True, **protocol}, indent=2)


@mcp.resource("formulary://medications")
def formulary() -> str:
    """The hospital formulary: known medications, high-alert designations, and the interaction
    reference used by `medication_interaction_check`."""
    known: set[str] = set()
    for key in INTERACTIONS["pairs"]:
        known.update(key.split("|"))

    return json.dumps(
        {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "note": "SYNTHETIC formulary generated for this capstone. Not a clinical reference.",
            "medication_count": len(known),
            "medications": sorted(known),
            "high_alert_medications": sorted(INTERACTIONS["high_alert"]),
            "interaction_pairs_known": len(INTERACTIONS["pairs"]),
            "severity_scale": ["minor", "moderate", "major", "contraindicated"],
            "escalation_rule": (
                "Any interaction of major or contraindicated severity requires pharmacist "
                "review before discharge medications are dispensed."
            ),
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
