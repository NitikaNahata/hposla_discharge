"""Gemini model access with timeouts, retries and explicit exit conditions (NFR-07).

Google Gemini is the only approved model provider for this project. Every structured-output call
in the graph goes through `invoke_structured`, which:

* binds a Pydantic schema so the return value is validated (AC-04),
* retries transient failures with exponential backoff, and
* raises a typed `LLMFailure` once the retry budget is exhausted, so the caller can route into
  the self-healing loop (AC-12) rather than crashing the run.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ValidationError

from .config import get_config
from .observability import extract_usage

T = TypeVar("T", bound=BaseModel)


class LLMFailure(RuntimeError):
    """Raised when the model could not produce a valid result within the retry budget."""

    def __init__(self, message: str, *, attempts: int, last_error: str = "") -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


def _accepts_temperature(model_name: str) -> bool:
    """Whether this model honours an explicit temperature.

    Gemini 3.x models use fixed sampling defaults and ignore the parameter, emitting a warning
    on every call. Omitting it there keeps the logs clean and the request honest about what is
    actually being controlled.
    """
    return not model_name.startswith(("gemini-3", "gemini-4"))


@lru_cache(maxsize=4)
def get_model(role: str = "worker") -> BaseChatModel:
    """Return a configured Gemini client. Cached per role so clients are reused.

    `role` is 'worker' or 'critic' — the critic is separately configurable so it can be
    raised to a stronger model without touching the workers.
    """
    cfg = get_config()
    cfg.require_api_key()
    model_name = cfg.critic_model if role == "critic" else cfg.model

    kwargs: dict[str, Any] = {
        "model": model_name,
        "google_api_key": cfg.google_api_key,
        "timeout": cfg.llm_timeout_seconds,
        # Retries are handled explicitly below so that each attempt can be traced.
        "max_retries": 0,
    }
    if _accepts_temperature(model_name):
        kwargs["temperature"] = cfg.temperature

    return ChatGoogleGenerativeAI(**kwargs)


def _is_retryable(exc: Exception) -> bool:
    """Transient failures worth retrying; deterministic ones are not."""
    text = f"{type(exc).__name__}: {exc}".lower()
    transient = (
        "timeout",
        "deadline",
        "unavailable",
        "429",
        "resource_exhausted",
        "rate limit",
        "500",
        "503",
        "internal error",
        "connection",
    )
    return any(marker in text for marker in transient)


def invoke_structured(
    messages: list[AnyMessage],
    schema: type[T],
    *,
    role: str = "worker",
    tracer: Any = None,
    node: str = "",
) -> T:
    """Invoke Gemini with a bound Pydantic schema and return a validated object.

    Raises `LLMFailure` when the retry budget is exhausted — the caller decides whether that
    becomes a self-healing retry, a fallback, or a run failure.
    """
    cfg = get_config()
    model = get_model(role)
    # `include_raw` keeps the underlying AIMessage alongside the parsed object, which is
    # the only place token usage is reported. Without it the counts are simply lost.
    runnable = model.with_structured_output(schema, include_raw=True)

    last_error = ""
    for attempt in range(1, cfg.llm_max_retries + 1):
        try:
            raw = runnable.invoke(messages)

            # With include_raw the runnable returns {"raw", "parsed", "parsing_error"}.
            if isinstance(raw, dict) and "parsed" in raw:
                # Record usage before checking for a parse error: a call that produced
                # unparseable output still cost tokens, and omitting it would understate
                # the true cost of a retry.
                if tracer is not None:
                    tracer.token_usage(node or role, extract_usage(raw.get("raw")))
                if raw.get("parsing_error") is not None:
                    raise ValueError(f"structured output parse failed: {raw['parsing_error']}")
                result = raw["parsed"]
            else:
                result = raw

            if not isinstance(result, schema):
                # Some providers return a dict; coerce and let Pydantic validate.
                result = schema.model_validate(result)
            if tracer is not None and node:
                tracer.structured_output(node, schema.__name__, ok=True)
            return result
        except ValidationError as exc:
            # The model answered but violated the contract. Retrying can help — the
            # error text is appended so the next attempt sees what was wrong.
            last_error = f"ValidationError: {exc}"
            if tracer is not None and node:
                tracer.structured_output(node, schema.__name__, ok=False, error=str(exc)[:400])
            messages = [
                *messages,
                HumanMessage(
                    "Your previous response failed schema validation with:\n"
                    f"{str(exc)[:800]}\n"
                    "Return a corrected response that satisfies the schema exactly."
                ),
            ]
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if tracer is not None:
                tracer.emit(
                    "llm_retry",
                    node=node,
                    attempt=attempt,
                    retryable=_is_retryable(exc),
                    error=last_error[:300],
                )
            if not _is_retryable(exc):
                raise LLMFailure(
                    f"Non-retryable model error in {node or role}: {last_error}",
                    attempts=attempt,
                    last_error=last_error,
                ) from exc

        if attempt < cfg.llm_max_retries:
            # Exponential backoff: 1s, 2s, 4s ...
            time.sleep(2 ** (attempt - 1))

    raise LLMFailure(
        f"Model failed after {cfg.llm_max_retries} attempts in {node or role}",
        attempts=cfg.llm_max_retries,
        last_error=last_error,
    )


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) for compression triggering (NFR-08).

    Deliberately approximate: it gates a summarization decision, not billing.
    """
    return max(1, len(text) // 4)
