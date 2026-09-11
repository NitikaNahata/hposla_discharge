"""AC-08 — A memory eviction / importance policy is implemented and documented.

The policy is importance-weighted with a TTL floor and a per-namespace LRU cap. The tests below
check the clinical consequence, not just the arithmetic: an allergy must survive indefinitely
while an incidental observation ages out.

Documented in `docs/memory-design.md`. Implementation: `memory/policy.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from discharge_copilot.memory.episodic import EpisodicMemory
from discharge_copilot.memory.policy import (
    EvictionPolicy,
    access_boost,
    base_importance,
    effective_importance,
    recency_decay,
)

NAMESPACE = "MRN-AC08"


def _record(key: str, kind: str, *, age_days: float = 0.0, access_count: int = 0) -> dict:
    created = datetime.now(UTC) - timedelta(days=age_days)
    return {
        "key": key,
        "kind": kind,
        "content": f"fact {key}",
        "importance": base_importance(kind),
        "created_at": created.isoformat(),
        "access_count": access_count,
    }


# ---------------------------------------------------------------------------
# Scoring components
# ---------------------------------------------------------------------------


def test_ac08_importance_ranks_clinical_significance():
    """AC-08: the importance table encodes what actually matters clinically."""
    assert base_importance("allergy") == 1.0
    assert base_importance("allergy") > base_importance("care_constraint")
    assert base_importance("care_constraint") > base_importance("medication_change")
    assert base_importance("medication_change") > base_importance("observation")
    assert base_importance("unknown_kind") == 0.30


def test_ac08_recency_decay_halves_at_the_half_life():
    """AC-08: decay is exponential with a documented half-life."""
    assert recency_decay(0) == 1.0
    assert recency_decay(180) == pytest.approx(0.5, abs=0.01)
    assert recency_decay(360) == pytest.approx(0.25, abs=0.01)
    assert recency_decay(30) > recency_decay(90)


def test_ac08_access_boost_saturates():
    """AC-08: repeated recall lifts a fact, but cannot lift it without limit."""
    assert access_boost(0) == 0.0
    assert access_boost(1) > 0
    assert access_boost(10) > access_boost(1)
    assert access_boost(1000) <= 0.15


def test_ac08_effective_importance_combines_all_three():
    """AC-08: effective = base × recency_decay + access_boost."""
    fresh = effective_importance(_record("k", "medication_change", age_days=0))
    stale = effective_importance(_record("k", "medication_change", age_days=365))
    used = effective_importance(_record("k", "medication_change", age_days=365, access_count=20))
    assert fresh > stale
    assert used > stale, "frequent recall should partly offset age"


# ---------------------------------------------------------------------------
# Stage 1 — TTL sweep
# ---------------------------------------------------------------------------


def test_ac08_an_allergy_is_never_ttl_evicted():
    """AC-08: a penicillin allergy does not stop being true after 30 days.

    This is the property that makes the policy safe to run at all. A pure TTL or LRU scheme
    would eventually drop it.
    """
    policy = EvictionPolicy(ttl_days=30)
    ancient_allergy = _record("allergy:penicillin", "allergy", age_days=5000)
    assert policy.is_permanent(ancient_allergy) is True
    assert policy.should_expire(ancient_allergy) is False


def test_ac08_an_incidental_observation_ages_out():
    """AC-08: low-importance colour is dropped once it is past its TTL."""
    policy = EvictionPolicy(ttl_days=30, importance_floor=0.25)
    old_note = _record("obs:spouse_drove", "observation", age_days=400)
    assert policy.is_permanent(old_note) is False
    assert policy.should_expire(old_note) is True


def test_ac08_a_recent_low_importance_fact_is_kept():
    """AC-08: TTL expiry needs both age and low effective importance, not either alone."""
    policy = EvictionPolicy(ttl_days=30)
    assert policy.should_expire(_record("obs", "observation", age_days=5)) is False


def test_ac08_care_constraints_are_permanent():
    """AC-08: 'lives alone with no caregiver' stays relevant across admissions."""
    policy = EvictionPolicy(ttl_days=30)
    assert policy.is_permanent(_record("c", "care_constraint", age_days=900)) is True
    assert policy.is_permanent(_record("c", "risk_tier", age_days=900)) is True


# ---------------------------------------------------------------------------
# Stage 2 — capacity sweep
# ---------------------------------------------------------------------------


def test_ac08_capacity_sweep_drops_the_least_important_first():
    """AC-08: over the cap, the lowest effective importance is evicted first."""
    policy = EvictionPolicy(ttl_days=3650, max_per_namespace=3)
    records = [
        _record("allergy:penicillin", "allergy"),
        _record("care_constraint:alone", "care_constraint"),
        _record("med_change:x", "medication_change"),
        _record("obs:1", "observation"),
        _record("obs:2", "observation"),
    ]
    plan = policy.plan_eviction(records)

    assert plan["kept"] == 3
    assert plan["evicted"] == 2
    kept_keys = {r["key"] for r in plan["keep"]}
    assert "allergy:penicillin" in kept_keys
    assert "care_constraint:alone" in kept_keys
    assert {r["key"] for r in plan["evict"]} == {"obs:1", "obs:2"}


def test_ac08_eviction_plan_records_an_auditable_reason():
    """AC-08: every eviction carries a reason, so the log is inspectable evidence."""
    policy = EvictionPolicy(ttl_days=30, max_per_namespace=2)
    plan = policy.plan_eviction(
        [
            _record("allergy:penicillin", "allergy"),
            _record("obs:old", "observation", age_days=400),
            _record("obs:a", "observation"),
            _record("obs:b", "observation"),
        ]
    )
    assert "ttl_expired" in plan["reasons"]["obs:old"]
    assert plan["expired_by_ttl"] == 1
    assert plan["evicted_by_capacity"] >= 1
    for key in (r["key"] for r in plan["evict"]):
        assert key in plan["reasons"]


def test_ac08_permanent_facts_are_counted_in_the_plan():
    """AC-08: the plan reports how many retained facts were protected by importance."""
    policy = EvictionPolicy(max_per_namespace=10)
    plan = policy.plan_eviction(
        [_record("allergy:p", "allergy"), _record("obs", "observation")]
    )
    assert plan["permanent_retained"] == 1


# ---------------------------------------------------------------------------
# Applied to the real store
# ---------------------------------------------------------------------------


def test_ac08_eviction_applies_to_the_episodic_store(tmp_path):
    """AC-08: the policy actually deletes rows, and keeps what it should."""
    mem = EpisodicMemory(
        db_path=tmp_path / "e.sqlite",
        policy=EvictionPolicy(ttl_days=3650, max_per_namespace=2),
    )
    mem.write(namespace=NAMESPACE, key="allergy:penicillin",
              content="Allergic to penicillin.", kind="allergy", session_id="s1")
    mem.write(namespace=NAMESPACE, key="care_constraint:alone",
              content="Lives alone.", kind="care_constraint", session_id="s1")
    for i in range(4):
        mem.write(namespace=NAMESPACE, key=f"obs:{i}", content=f"Observation {i}.",
                  kind="observation", session_id="s1")

    assert mem.count(NAMESPACE) == 6
    result = mem.evict(NAMESPACE)

    assert result["evicted"] == 4
    assert mem.count(NAMESPACE) == 2
    remaining = {r["key"] for r in mem.recall(NAMESPACE, touch=False)}
    assert remaining == {"allergy:penicillin", "care_constraint:alone"}


def test_ac08_dry_run_reports_without_deleting(tmp_path):
    """AC-08: the policy can be inspected before it is applied."""
    mem = EpisodicMemory(
        db_path=tmp_path / "e.sqlite", policy=EvictionPolicy(max_per_namespace=1)
    )
    mem.write(namespace=NAMESPACE, key="allergy:p", content="Allergic.",
              kind="allergy", session_id="s1")
    mem.write(namespace=NAMESPACE, key="obs", content="Note.",
              kind="observation", session_id="s1")

    result = mem.evict(NAMESPACE, dry_run=True)
    assert result["evicted"] == 1
    assert result["dry_run"] is True
    assert mem.count(NAMESPACE) == 2, "a dry run must not delete anything"


def test_ac08_eviction_is_namespaced(tmp_path):
    """AC-08: evicting one patient's memory never touches another's."""
    mem = EpisodicMemory(
        db_path=tmp_path / "e.sqlite", policy=EvictionPolicy(max_per_namespace=1)
    )
    for ns in ("MRN-A", "MRN-B"):
        mem.write(namespace=ns, key="allergy:p", content="Allergic.", kind="allergy",
                  session_id="s1")
        mem.write(namespace=ns, key="obs", content="Note.", kind="observation",
                  session_id="s1")
    mem.evict("MRN-A")
    assert mem.count("MRN-A") == 1
    assert mem.count("MRN-B") == 2
