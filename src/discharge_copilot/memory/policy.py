"""Memory importance and eviction policy (AC-08).

The policy is **importance-weighted with a TTL floor and a per-namespace LRU cap**. Three
mechanisms, because no single one is right on its own:

* **Importance weighting** decides what is worth keeping. "Allergic to penicillin" and "has no
  transport to appointments" are not equally durable facts, and a pure recency or LRU policy
  cannot tell them apart.
* **Recency decay** stops a fact learned two years ago from outranking one learned last week,
  without deleting it outright.
* **Access boost** lets facts that keep proving useful earn their place, which pure importance
  scoring cannot express.

    effective = base_importance × recency_decay(age) + access_boost(access_count)

Eviction is a two-stage sweep:

1. **TTL sweep** — anything below the importance floor whose age exceeds the TTL is dropped.
   High-importance facts are exempt from TTL entirely: a penicillin allergy does not stop being
   true after 30 days.
2. **Capacity sweep** — if a namespace still exceeds its cap, the lowest-effective-importance
   entries are dropped until it fits.

The clinical consequence is the point: an allergy survives indefinitely, while "spouse drove him
home on this admission" ages out. Tested at both ends in `tests/test_ac08_eviction_policy.py`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Base importance by fact category. These are policy, so they live in one visible table
# rather than being scattered through the extraction code.
IMPORTANCE_BY_KIND: dict[str, float] = {
    "allergy": 1.00,            # never safe to forget
    "adverse_reaction": 0.95,
    "high_alert_medication": 0.85,
    "care_constraint": 0.80,    # lives alone, no caregiver, no transport
    "risk_tier": 0.75,
    "adherence_concern": 0.70,
    "medication_change": 0.60,
    "language_preference": 0.60,
    "caregiver": 0.65,
    "diagnosis": 0.55,
    "followup_commitment": 0.50,
    "escalation": 0.50,
    "admission_event": 0.40,
    "observation": 0.25,        # incidental colour
}

DEFAULT_IMPORTANCE = 0.30

# Half-life for recency decay, in days. A fact keeps half its weight after this long.
RECENCY_HALF_LIFE_DAYS = 180.0

# How much repeated recall can lift a fact, and how fast it saturates.
ACCESS_BOOST_CEILING = 0.15
ACCESS_BOOST_RATE = 0.05


def base_importance(kind: str) -> float:
    """Base importance for a fact category."""
    return IMPORTANCE_BY_KIND.get(kind, DEFAULT_IMPORTANCE)


def recency_decay(age_days: float, half_life: float = RECENCY_HALF_LIFE_DAYS) -> float:
    """Exponential decay factor in (0, 1]."""
    if age_days <= 0:
        return 1.0
    return math.pow(0.5, age_days / half_life)


def access_boost(access_count: int) -> float:
    """Diminishing bonus for facts that keep being recalled."""
    if access_count <= 0:
        return 0.0
    return ACCESS_BOOST_CEILING * (1.0 - math.exp(-ACCESS_BOOST_RATE * access_count))


def _age_days(created_at: str, now: datetime | None = None) -> float:
    """Age of a fact in days, tolerant of a malformed timestamp."""
    now = now or datetime.now(UTC)
    try:
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return 0.0
    return max((now - created).total_seconds() / 86400.0, 0.0)


def effective_importance(record: dict[str, Any], now: datetime | None = None) -> float:
    """Current effective importance of a stored fact."""
    base = float(record.get("importance", DEFAULT_IMPORTANCE))
    age = _age_days(record.get("created_at", ""), now)
    score = base * recency_decay(age) + access_boost(int(record.get("access_count", 0)))
    return round(min(score, 1.0), 4)


@dataclass(frozen=True)
class EvictionPolicy:
    """Retention rules for one memory namespace."""

    ttl_days: int = 30
    max_per_namespace: int = 50
    importance_floor: float = 0.25
    # Facts at or above this base importance are never TTL-evicted.
    permanent_threshold: float = 0.75

    def is_permanent(self, record: dict[str, Any]) -> bool:
        """Whether a fact is exempt from TTL expiry on importance grounds."""
        return float(record.get("importance", 0.0)) >= self.permanent_threshold

    def should_expire(self, record: dict[str, Any], now: datetime | None = None) -> bool:
        """Stage 1: TTL sweep on low-importance facts."""
        if self.is_permanent(record):
            return False
        if _age_days(record.get("created_at", ""), now) <= self.ttl_days:
            return False
        return effective_importance(record, now) < self.importance_floor

    def plan_eviction(
        self, records: Iterable[dict[str, Any]], now: datetime | None = None
    ) -> dict[str, Any]:
        """Decide what to evict from one namespace.

        Returns the keep and evict sets plus a per-record reason, so the decision is
        auditable in `evidence/logs/ac08_eviction.log` rather than opaque.
        """
        items = list(records)
        scored = [
            {**r, "effective_importance": effective_importance(r, now)} for r in items
        ]

        expired = [r for r in scored if self.should_expire(r, now)]
        survivors = [r for r in scored if r not in expired]

        # Stage 2: capacity sweep, lowest effective importance first.
        survivors.sort(key=lambda r: r["effective_importance"], reverse=True)
        over_capacity = survivors[self.max_per_namespace :]
        keep = survivors[: self.max_per_namespace]

        reasons: dict[str, str] = {}
        for r in expired:
            reasons[r["key"]] = (
                f"ttl_expired: age > {self.ttl_days}d and effective importance "
                f"{r['effective_importance']:.3f} < floor {self.importance_floor}"
            )
        for r in over_capacity:
            reasons[r["key"]] = (
                f"capacity: namespace over cap of {self.max_per_namespace}; "
                f"lowest effective importance {r['effective_importance']:.3f}"
            )

        return {
            "keep": keep,
            "evict": expired + over_capacity,
            "reasons": reasons,
            "evaluated": len(items),
            "kept": len(keep),
            "evicted": len(expired) + len(over_capacity),
            "expired_by_ttl": len(expired),
            "evicted_by_capacity": len(over_capacity),
            "permanent_retained": sum(1 for r in keep if self.is_permanent(r)),
        }


def policy_from_config() -> EvictionPolicy:
    """Build the policy from environment configuration."""
    from ..config import get_config

    cfg = get_config()
    return EvictionPolicy(
        ttl_days=cfg.memory_ttl_days,
        max_per_namespace=cfg.memory_max_per_namespace,
        importance_floor=cfg.memory_importance_floor,
    )
