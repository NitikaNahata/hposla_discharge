"""Environment-driven configuration (NFR-01, NFR-07).

Every knob is an environment variable with a sane default, templated in `.env.example`.
No secret is ever hard-coded or committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_str(key: str, default: str) -> str:
    value = os.getenv(key, "").strip()
    return value or default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else REPO_ROOT / path


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration."""

    # --- Model provider: Google Gemini is the only approved provider ---
    google_api_key: str = ""
    model: str = "gemini-3.6-flash"
    critic_model: str = "gemini-3.6-flash"
    temperature: float = 0.1

    # --- Reliability: timeouts, retries, explicit exit conditions (NFR-07) ---
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3
    max_supervisor_steps: int = 12
    max_self_heal_retries: int = 2
    mcp_timeout_seconds: int = 30

    # --- Context engineering (NFR-08) ---
    working_memory_window: int = 8
    compression_trigger_tokens: int = 3000

    # --- Memory tiers and eviction (AC-06..AC-08) ---
    state_dir: Path = field(default_factory=lambda: REPO_ROOT / ".state")
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    memory_ttl_days: int = 30
    memory_max_per_namespace: int = 50
    memory_importance_floor: float = 0.25

    # --- Evidence (NFR-04, NFR-05) ---
    evidence_dir: Path = field(default_factory=lambda: REPO_ROOT / "evidence")
    redact_pii: bool = True

    # --- Repository-relative content roots ---
    knowledge_dir: Path = field(default_factory=lambda: REPO_ROOT / "knowledge")
    samples_dir: Path = field(default_factory=lambda: REPO_ROOT / "data" / "samples")
    mcp_server_script: Path = field(
        default_factory=lambda: REPO_ROOT / "mcp_server" / "discharge_server.py"
    )

    # --- Derived paths ---
    @property
    def checkpoint_db(self) -> Path:
        return self.state_dir / "checkpoints.sqlite"

    @property
    def episodic_db(self) -> Path:
        return self.state_dir / "episodic_memory.sqlite"

    @property
    def chroma_dir(self) -> Path:
        return self.state_dir / "chroma"

    @property
    def traces_dir(self) -> Path:
        return self.evidence_dir / "traces"

    @property
    def transcripts_dir(self) -> Path:
        return self.evidence_dir / "transcripts"

    @property
    def logs_dir(self) -> Path:
        return self.evidence_dir / "logs"

    def ensure_dirs(self) -> None:
        """Create every runtime directory. Safe to call repeatedly."""
        for path in (
            self.state_dir,
            self.chroma_dir,
            self.traces_dir,
            self.transcripts_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def require_api_key(self) -> str:
        """Fail loudly and usefully when the key is missing."""
        if not self.google_api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set.\n"
                "  cp .env.example .env   then add your Gemini API key.\n"
                "  Get one at https://aistudio.google.com/apikey"
            )
        return self.google_api_key


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Load configuration once per process."""
    return Config(
        google_api_key=_env_str("GOOGLE_API_KEY", ""),
        model=_env_str("DISCHARGE_MODEL", "gemini-3.6-flash"),
        critic_model=_env_str("DISCHARGE_CRITIC_MODEL", "gemini-3.6-flash"),
        temperature=_env_float("DISCHARGE_TEMPERATURE", 0.1),
        llm_timeout_seconds=_env_int("DISCHARGE_LLM_TIMEOUT_SECONDS", 60),
        llm_max_retries=_env_int("DISCHARGE_LLM_MAX_RETRIES", 3),
        max_supervisor_steps=_env_int("DISCHARGE_MAX_SUPERVISOR_STEPS", 12),
        max_self_heal_retries=_env_int("DISCHARGE_MAX_SELF_HEAL_RETRIES", 2),
        mcp_timeout_seconds=_env_int("DISCHARGE_MCP_TIMEOUT_SECONDS", 30),
        working_memory_window=_env_int("DISCHARGE_WORKING_MEMORY_WINDOW", 8),
        compression_trigger_tokens=_env_int("DISCHARGE_COMPRESSION_TRIGGER_TOKENS", 3000),
        state_dir=_resolve(_env_str("DISCHARGE_STATE_DIR", ".state")),
        embedding_model=_env_str(
            "DISCHARGE_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        ),
        memory_ttl_days=_env_int("DISCHARGE_MEMORY_TTL_DAYS", 30),
        memory_max_per_namespace=_env_int("DISCHARGE_MEMORY_MAX_PER_NAMESPACE", 50),
        memory_importance_floor=_env_float("DISCHARGE_MEMORY_IMPORTANCE_FLOOR", 0.25),
        evidence_dir=_resolve(_env_str("DISCHARGE_EVIDENCE_DIR", "evidence")),
        redact_pii=_env_bool("DISCHARGE_REDACT_PII", True),
    )
