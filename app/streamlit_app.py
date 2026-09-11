"""Streamlit UI — routing decisions and memory state (Good-to-Have).

    streamlit run app/streamlit_app.py

Deliberately a *diagnostic* view rather than a clinical one. The business case says interface
polish is not evaluated, so this shows the things that are otherwise invisible: which conditional
edges fired and why, what the critic rejected, which tools the agent chose to call, and what the
system remembers about a patient across sessions.

It reads committed traces from `evidence/traces/` — so it works on a fresh clone with no API key
— and can also run a live case when one is configured.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from discharge_copilot.config import get_config

CFG = get_config()

st.set_page_config(
    page_title="Discharge Copilot", page_icon="🏥", layout="wide",
)

EVENT_STYLE = {
    "routing_decision": ("🔀", "Routing"),
    "supervisor_decision": ("🧭", "Supervisor"),
    "reflection": ("🔍", "Critic"),
    "tool_call": ("🔧", "Tool"),
    "rag_query": ("📚", "Agentic RAG"),
    "quarantine_flag": ("🛡️", "Quarantine"),
    "risk_assessment": ("⚠️", "Risk"),
    "memory_op": ("🧠", "Memory"),
    "compression": ("🗜️", "Compression"),
    "tool_failure": ("💥", "Tool failure"),
    "enhanced_pathway_applied": ("➕", "Enhanced pathway"),
    "human_in_the_loop": ("⏸️", "Human review"),
}


@st.cache_data(show_spinner=False)
def load_trace(path_str: str) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path_str).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def available_traces() -> list[Path]:
    return sorted(CFG.traces_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("🏥 Discharge Copilot")
st.sidebar.caption("Multi-agent discharge coordination · LangGraph + MCP + tiered memory")

view = st.sidebar.radio(
    "View",
    ["Run trace", "Routing decisions", "Memory", "Evidence index"],
    label_visibility="collapsed",
)

traces = available_traces()
if not traces:
    st.sidebar.warning("No traces yet. Run `./run.sh` to generate them.")

selected_trace = None
if traces and view in {"Run trace", "Routing decisions"}:
    selected_trace = st.sidebar.selectbox(
        "Trace", traces, format_func=lambda p: p.stem
    )

st.sidebar.divider()
st.sidebar.caption(
    "All data is synthetic. This is a coordination aid, not medical advice."
)


# ---------------------------------------------------------------------------
# Run trace
# ---------------------------------------------------------------------------

if view == "Run trace":
    st.title("Run trace")
    if not selected_trace:
        st.info("No trace selected. Run `./run.sh` first.")
        st.stop()

    events = load_trace(str(selected_trace))
    st.caption(f"`{selected_trace.name}` · {len(events)} events")

    counts: dict[str, int] = {}
    for e in events:
        counts[e["event"]] = counts.get(e["event"], 0) + 1

    cols = st.columns(6)
    cols[0].metric("Nodes run", counts.get("node_enter", 0))
    cols[1].metric("Routing decisions", counts.get("routing_decision", 0))
    cols[2].metric("Tool calls", counts.get("tool_call", 0))
    cols[3].metric("RAG lookups", counts.get("rag_query", 0))
    cols[4].metric("Critic verdicts", counts.get("reflection", 0))
    cols[5].metric("Tool failures", counts.get("tool_failure", 0))

    reflections = [e for e in events if e["event"] == "reflection"]
    if reflections:
        st.subheader("Critic verdicts")
        for r in reflections:
            colour = {"accept": "green", "revise": "orange", "escalate": "red"}.get(
                r["action"], "grey"
            )
            with st.expander(
                f":{colour}[{r['action'].upper()}] · **{r['worker']}** · "
                f"confidence {r['confidence']:.2f}",
                expanded=r["action"] != "accept",
            ):
                if r.get("issues"):
                    for issue in r["issues"]:
                        st.markdown(f"- {issue}")
                else:
                    st.caption("No issues found.")

    st.subheader("Timeline")
    show_all = st.checkbox("Show every event", value=False)
    for e in events:
        style = EVENT_STYLE.get(e["event"])
        if style is None and not show_all:
            continue
        icon, label = style or ("·", e["event"])
        detail = {
            k: v for k, v in e.items()
            if k not in {"seq", "ts", "elapsed_ms", "trace_id", "case_id", "event"}
        }
        summary = {
            "routing_decision": lambda x: f"`{x['router']}` → **{x['decision']}** — {x['reason']}",
            "supervisor_decision": lambda x: f"→ **{x['decision']}** — {x.get('reason', '')}",
            "reflection": lambda x: (
                f"**{x['worker']}** confidence {x['confidence']:.2f} → "
                f"**{x['action'].upper()}**"
            ),
            "tool_call": lambda x: (
                f"`{x['tool']}` ({x['source']}) — {'ok' if x.get('ok') else 'FAILED'}"
            ),
            "rag_query": lambda x: f"_\"{x['query']}\"_ → {x['sources']}",
            "quarantine_flag": lambda x: (
                f"{x['flagged_count']}/{x['note_count']} note(s) flagged: {x['flags']}"
            ),
            "risk_assessment": lambda x: (
                f"tier **{x['tier']}** (score {x['score']:.2f})"
            ),
        }.get(e["event"])
        text = summary(e) if summary else f"`{json.dumps(detail)[:220]}`"
        st.markdown(f"{icon} `{e['elapsed_ms']:>7.0f}ms` **{label}** — {text}")


# ---------------------------------------------------------------------------
# Routing decisions
# ---------------------------------------------------------------------------

elif view == "Routing decisions":
    st.title("Conditional routing")
    st.caption(
        "Four routers, each a pure function of state. This view shows which branch each one "
        "took and why (AC-03)."
    )
    if not selected_trace:
        st.info("No trace selected.")
        st.stop()

    events = load_trace(str(selected_trace))
    decisions = [e for e in events if e["event"] == "routing_decision"]

    if not decisions:
        st.warning("No routing decisions in this trace.")
        st.stop()

    by_router: dict[str, list[dict]] = {}
    for d in decisions:
        by_router.setdefault(d["router"], []).append(d)

    for router, rows in by_router.items():
        st.subheader(f"`{router}`")
        branches = sorted({r["decision"] for r in rows})
        st.caption(f"{len(rows)} decision(s) · branches taken: {', '.join(branches)}")
        st.dataframe(
            [
                {
                    "at": f"{r['elapsed_ms']:.0f}ms",
                    "decision": r["decision"],
                    "reason": r["reason"],
                    "completed so far": ", ".join(r.get("completed", [])) or "—",
                }
                for r in rows
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.subheader("Graph topology")
    st.markdown(
        """
```
intake ─▶ supervisor ─(route_from_supervisor)─▶ summary | medication | followup
                                                 | education | finalize
medication ─(route_after_medication)─▶ pharmacist_review | reflect
followup   ─(route_risk_tier)───────▶ enhanced_followup | reflect
reflect    ─(route_after_reflection)▶ <same worker> | compress | supervisor
```
"""
    )


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

elif view == "Memory":
    st.title("Tiered memory")
    st.caption(
        "T1 working (in-state) · T2 episodic (SQLite) · T3 semantic (Chroma). "
        "The durable tiers persist across sessions (AC-06, AC-07, AC-08)."
    )

    from discharge_copilot.memory import TieredMemory

    memory = TieredMemory()
    patients = memory.episodic.namespaces()

    if not patients:
        st.info("No memory stored yet. Run a case with `./run.sh` first.")
        st.stop()

    mrn = st.selectbox("Patient", patients)
    snapshot = memory.snapshot(mrn)

    cols = st.columns(3)
    cols[0].metric("Episodic facts", snapshot["episodic_count"])
    cols[1].metric("Semantic facts", snapshot["semantic_count"])
    cols[2].metric("Sessions", len(snapshot["sessions"]))

    if len(snapshot["sessions"]) > 1:
        st.success(
            f"Memory spans {len(snapshot['sessions'])} sessions "
            f"({', '.join(snapshot['sessions'])}) — cross-session persistence (AC-07)."
        )

    st.subheader("Stored facts")
    st.dataframe(
        [
            {
                "key": f["key"],
                "kind": f["kind"],
                "base": round(f["base_importance"], 2),
                "effective": round(f["effective_importance"], 3),
                "permanent": "yes" if f["permanent"] else "",
                "hits": f["access_count"],
                "session": f["session_id"],
                "fact": f["content"],
            }
            for f in snapshot["facts"]
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "`permanent` facts are exempt from TTL expiry — an allergy does not stop being true "
        "after 30 days (AC-08)."
    )

    st.subheader("Semantic recall")
    query = st.text_input(
        "Ask what the system knows",
        value="why might this patient not attend her follow-up appointment?",
    )
    if query:
        with st.spinner("Searching memory…"):
            hits = memory.recall_for_patient(mrn, query, k=5)
        for hit in hits:
            st.markdown(
                f"- `[{hit['tier']}]` {hit['content']}  \n"
                f"  <span style='opacity:.6'>importance {hit['importance']:.2f} · "
                f"score {hit['score']:.2f} · written in {hit['session_id']}</span>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Evidence index
# ---------------------------------------------------------------------------

else:
    st.title("Committed evidence")
    st.caption("Only committed artifacts are scored — the Evidence-in-Repo Rule.")

    for label, directory in (
        ("Transcripts", CFG.transcripts_dir),
        ("Logs", CFG.logs_dir),
        ("Traces", CFG.traces_dir),
    ):
        files = sorted(directory.glob("*"))
        st.subheader(f"{label} ({len(files)})")
        if not files:
            st.caption("None yet — run `./run.sh`.")
            continue
        for path in files:
            with st.expander(f"`{path.name}` · {path.stat().st_size:,} bytes"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                if path.suffix == ".jsonl":
                    st.code(
                        "\n".join(text.splitlines()[:40]), language="json"
                    )
                else:
                    st.markdown(text[:12000])
