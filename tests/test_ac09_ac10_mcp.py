"""AC-09 / AC-10 — Custom MCP server and its integration via langchain-mcp-adapters.

AC-09: the server exposes >= 2 tools and >= 1 resource relevant to discharge planning.
AC-10: the agent consumes it through `langchain-mcp-adapters`, evidenced by a tool-call
transcript.

The server tests run it as a real subprocess over stdio — the same transport the agent uses.
Testing the module by direct import would skip the protocol layer, which is the part most likely
to break.
"""

from __future__ import annotations

import json

import pytest

from discharge_copilot.tools.mcp_client import (
    EXPECTED_RESOURCES,
    EXPECTED_TOOLS,
    DischargeMCPClient,
)


@pytest.fixture(scope="module")
def mcp_client():
    """One connected client for the module — spawning the server is the slow part."""
    client = DischargeMCPClient()
    tools = client.load_tools()
    if not tools:
        pytest.fail(f"MCP server failed to start: {client.last_error}")
    yield client
    client.close()


@pytest.fixture(scope="module")
def tools_by_name(mcp_client):
    return {t.name: t for t in mcp_client.load_tools()}


# ---------------------------------------------------------------------------
# AC-09 — the server's published surface
# ---------------------------------------------------------------------------


def test_ac09_server_exposes_at_least_two_tools(tools_by_name):
    """AC-09: the criterion requires >= 2 tools; the server publishes 4."""
    assert len(tools_by_name) >= 2
    assert set(EXPECTED_TOOLS) <= set(tools_by_name)


def test_ac09_server_exposes_at_least_one_resource(mcp_client):
    """AC-09: the criterion requires >= 1 resource; the server publishes 2.

    One is static (`formulary://medications`) and one is templated
    (`discharge://protocol/{condition}`). MCP lists those separately, so an inventory that
    only read `resources/list` would silently under-report the server.
    """
    inventory = mcp_client.list_inventory()
    assert inventory["connected"] is True
    assert inventory["resource_count"] >= 1
    assert "formulary://medications" in inventory["resources"]
    assert "discharge://protocol/{condition}" in inventory["resource_templates"]
    assert set(EXPECTED_RESOURCES) <= set(
        inventory["resources"] + inventory["resource_templates"]
    )


def test_ac09_every_tool_carries_a_description_for_the_model(tools_by_name):
    """AC-09: an MCP tool with no description is unusable by a model choosing tools."""
    for name, tool in tools_by_name.items():
        assert tool.description and len(tool.description) > 40, f"{name} is under-documented"
        assert tool.args_schema is not None


def test_ac09_patient_lookup_returns_a_record(tools_by_name):
    """AC-09: patient_lookup resolves a known synthetic MRN."""
    result = json.loads(tools_by_name["patient_lookup"].invoke({"mrn": "MRN-2001"}))
    assert result["found"] is True
    assert result["mrn"] == "MRN-2001"
    assert "Penicillin" in result["allergies"]
    assert result["pre_admission_medications"]


def test_ac09_patient_lookup_handles_an_unknown_mrn(tools_by_name):
    """AC-09/NFR-07: an unknown record returns structured data, not an exception."""
    result = json.loads(tools_by_name["patient_lookup"].invoke({"mrn": "MRN-NOPE"}))
    assert result["found"] is False
    assert "available_mrns" in result


@pytest.mark.parametrize(
    "meds,expected_severity",
    [
        (["Warfarin", "Fluconazole"], "contraindicated"),
        (["Warfarin", "Amiodarone"], "major"),
        (["Furosemide", "Ibuprofen"], "major"),
        (["Omeprazole", "Warfarin"], "moderate"),
        (["Lisinopril", "Tiotropium"], "none"),
    ],
)
def test_ac09_interaction_check_grades_severity(tools_by_name, meds, expected_severity):
    """AC-09: severity grading is what the pharmacist-review routing depends on (AC-03)."""
    result = json.loads(
        tools_by_name["medication_interaction_check"].invoke({"medications": meds})
    )
    assert result["highest_severity"] == expected_severity


def test_ac09_interaction_check_strips_doses_from_names(tools_by_name):
    """AC-09: real medication lists carry doses; the tool must still match the pair."""
    result = json.loads(
        tools_by_name["medication_interaction_check"].invoke(
            {"medications": ["Warfarin 7.5 mg once daily", "Fluconazole 100 mg once daily"]}
        )
    )
    assert result["highest_severity"] == "contraindicated"
    assert result["pharmacist_review_required"] is True
    assert set(result["medications_checked"]) == {"warfarin", "fluconazole"}


def test_ac09_interaction_check_flags_high_alert_medications(tools_by_name):
    """AC-09: high-alert medications are surfaced even when no pair interacts."""
    result = json.loads(
        tools_by_name["medication_interaction_check"].invoke({"medications": ["Warfarin"]})
    )
    assert "warfarin" in result["high_alert_medications"]


def test_ac09_schedule_followup_books_within_the_window(tools_by_name):
    """AC-09: scheduling honours the clinical window when a slot exists."""
    result = json.loads(
        tools_by_name["schedule_followup"].invoke(
            {"mrn": "MRN-3001", "specialty": "Anticoagulation Clinic",
             "within_days": 7, "reason": "INR check", "discharge_date": "2026-09-06"}
        )
    )
    assert result["confirmed"] is True
    assert result["within_requested_window"] is True
    assert result["days_from_discharge"] <= 7
    assert result["confirmation_id"]


def test_ac09_schedule_followup_reports_when_no_slot_fits(tools_by_name):
    """AC-09: an impossible window returns the next slot flagged as out of window.

    Silently booking outside the clinical window would be worse than failing — the plan would
    look satisfied while the monitoring requirement went unmet.
    """
    result = json.loads(
        tools_by_name["schedule_followup"].invoke(
            {"mrn": "MRN-1001", "specialty": "Pulmonology",
             "within_days": 1, "reason": "urgent review", "discharge_date": "2026-09-06"}
        )
    )
    assert result["within_requested_window"] is False
    assert "NO SLOT" in result["note"]


def test_ac09_transport_tool_validates_its_input(tools_by_name):
    """AC-09: an unknown transport type returns the valid set rather than failing opaquely."""
    result = json.loads(
        tools_by_name["check_transport_availability"].invoke(
            {"mrn": "MRN-2001", "transport_type": "helicopter"}
        )
    )
    assert result["available"] is False
    assert "wheelchair_van" in result["valid_types"]


def test_ac09_protocol_resource_resolves_aliases(mcp_client):
    """AC-09: the protocol resource resolves common clinical aliases."""
    for alias in ("heart-failure", "chf", "congestive heart failure"):
        payload = json.loads(mcp_client.read_resource(f"discharge://protocol/{alias}"))
        assert payload["found"] is True, f"alias '{alias}' did not resolve"
        assert "follow_up" in payload
        assert payload["red_flags"]


def test_ac09_protocol_resource_reports_an_unknown_condition(mcp_client):
    """AC-09: an unknown condition lists what is available instead of erroring."""
    payload = json.loads(mcp_client.read_resource("discharge://protocol/dragonpox"))
    assert payload["found"] is False
    assert "pneumonia" in payload["available"]


def test_ac09_formulary_resource_lists_high_alert_medications(mcp_client):
    """AC-09: the formulary resource carries the escalation rule the workers rely on."""
    payload = json.loads(mcp_client.read_resource("formulary://medications"))
    assert payload["medication_count"] > 10
    assert "warfarin" in payload["high_alert_medications"]
    assert payload["severity_scale"] == ["minor", "moderate", "major", "contraindicated"]


# ---------------------------------------------------------------------------
# AC-10 — adapter integration
# ---------------------------------------------------------------------------


def test_ac10_tools_load_through_langchain_mcp_adapters(mcp_client, tools_by_name):
    """AC-10: tools arrive as LangChain BaseTools via the mandated adapter."""
    from langchain_core.tools import BaseTool

    inventory = mcp_client.list_inventory()
    assert inventory["adapter"] == "langchain-mcp-adapters"
    assert inventory["transport"] == "stdio"
    for tool in tools_by_name.values():
        assert isinstance(tool, BaseTool)


def test_ac10_tool_calls_are_traced_for_the_transcript(tmp_tracer):
    """AC-10: every MCP invocation lands in the trace — the committed transcript evidence."""
    client = DischargeMCPClient(tracer=tmp_tracer)
    tools = {t.name: t for t in client.load_tools()}
    tools["patient_lookup"].invoke({"mrn": "MRN-1001"})

    events = [e for e in tmp_tracer.events if e["event"] == "tool_call"]
    assert events, "no tool_call event was traced"
    assert events[-1]["tool"] == "patient_lookup"
    assert events[-1]["source"] == "mcp"
    assert events[-1]["ok"] is True
    assert any(e["event"] == "mcp_connected" for e in tmp_tracer.events)
    client.close()


def test_ac10_results_are_unwrapped_from_mcp_content_blocks(tools_by_name):
    """AC-10: protocol content blocks are flattened before the model sees them.

    MCP returns `[{"type": "text", "text": "..."}]`. Passing that through raw makes every
    payload arrive wrapped in protocol noise the model then has to parse.
    """
    raw = tools_by_name["patient_lookup"].invoke({"mrn": "MRN-1001"})
    assert isinstance(raw, str)
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    assert parsed["mrn"] == "MRN-1001"


def test_ac10_a_tool_failure_degrades_instead_of_raising(tmp_tracer):
    """AC-10/AC-12/NFR-07: a forced server failure returns a readable observation.

    The self-healing loop can only act on a failure it is told about. A raised exception would
    kill the run; a message the model can read lets it note the gap and continue.
    """
    client = DischargeMCPClient(tracer=tmp_tracer, fault="error")
    tools = client.load_tools()
    if not tools:
        # The failure surfaced at connect time, which is itself graceful degradation.
        assert client.last_error
        assert any(e["event"] == "mcp_connect_failed" for e in tmp_tracer.events)
        return

    result = {t.name: t for t in tools}["patient_lookup"].invoke({"mrn": "MRN-1001"})
    assert "TOOL ERROR" in result or "TOOL TIMEOUT" in result
    assert any(e["event"] == "tool_failure" for e in tmp_tracer.events)
    client.close()


def test_ac10_toolbox_assembles_mcp_and_rag_together(tmp_tracer):
    """AC-10/AC-11: the workers receive both the MCP tools and the in-process RAG tool."""
    from discharge_copilot.tools import build_toolbox

    tools, client = build_toolbox(tmp_tracer)
    names = {t.name for t in tools}
    assert set(EXPECTED_TOOLS) <= names
    assert "search_clinical_guidance" in names
    if client:
        client.close()
