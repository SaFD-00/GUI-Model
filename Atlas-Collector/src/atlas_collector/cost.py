"""Per-call LLM token and cost accounting.

Atlas-Collector calls an LLM for exactly one thing — generating input text for
text fields — so the bill should be small. This module exists to keep that claim
falsifiable rather than assumed: every call is appended to a CSV with its step,
its caller, its token counts and its dollar cost, so a run that quietly starts
spending can be caught by reading a file instead of by reading a statement.

Rows are attributed to a ``step`` and an ``agent`` label rather than aggregated,
because the useful questions are per-step ("did input generation fire on every
screen, or only on the ones with fields?") and per-caller ("what else started
calling the LLM?"). A running total is the one thing a sum over rows cannot
cheaply answer mid-run, so it is carried on every row.

PRICING IS A SNAPSHOT, NOT A FACT
=================================

:data:`MODEL_PRICING` is USD per 1M tokens, hand-copied from the provider's
page. Prices change and model slugs get re-pointed. An unknown model is NOT an
error — it is priced at zero and logged once — because a wrong price must never
be the thing that aborts a collection run. The consequence is that ``cost_usd``
can silently read 0.00 for a model that is in fact billing, so treat the token
counts as the primary evidence and the dollars as a convenience.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

CSV_COLUMNS = (
    "timestamp_sec",
    "step",
    "agent",
    "model",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "total_usd",
)

#: USD per 1M tokens. Verify against https://openrouter.ai/models — slugs and
#: prices both move. Unknown models fall through to 0.0; see the module docstring.
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Default model for input-text generation (measured 2026-08-28).
    "qwen/qwen3.8-flash": {"input": 0.15, "output": 0.47},
    "qwen/qwen3.8-27b": {"input": 0.425, "output": 2.55},
    "qwen/qwen3.8-max": {"input": 2.00, "output": 6.00},
    "qwen/qwen3.7-flash": {"input": 0.03, "output": 0.13},
    "qwen/qwen3.7-plus": {"input": 0.32, "output": 1.28},
}

_PER_MILLION = 1_000_000


def price_of(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD for one call. Unknown models cost 0.0 (see the module docstring)."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0
    return (
        input_tokens * pricing["input"] + output_tokens * pricing["output"]
    ) / _PER_MILLION


@dataclass
class CostTracker:
    """Appends one row per LLM call and carries the running total."""

    csv_path: Path | None = None
    _total: float = field(default=0.0, init=False)
    _calls: int = field(default=0, init=False)
    _tokens_in: int = field(default=0, init=False)
    _tokens_out: int = field(default=0, init=False)
    _started: float = field(default=0.0, init=False)
    _step: int = field(default=-1, init=False)
    _unpriced: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self._started = time.monotonic()

    # -- lifecycle -----------------------------------------------------------

    def open(self, path: str | Path) -> None:
        """Create the CSV and write its header. Resets counters and the timer."""
        self.csv_path = Path(path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(CSV_COLUMNS)
        self._total = 0.0
        self._calls = 0
        self._tokens_in = 0
        self._tokens_out = 0
        self._unpriced = set()
        self._started = time.monotonic()

    def set_step(self, step: int) -> None:
        """Set the step every subsequent call is attributed to.

        Held on the tracker rather than threaded through every call site so that
        a new LLM consumer cannot forget to pass it and land its rows on step -1.
        """
        self._step = step

    # -- recording -----------------------------------------------------------

    def record(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        agent: str = "input_text",
        step: int | None = None,
    ) -> float:
        """Log one call and return its cost in USD."""
        cost = price_of(model, input_tokens, output_tokens)
        if model not in MODEL_PRICING and model not in self._unpriced:
            self._unpriced.add(model)
            logger.warning(
                "no price for model {!r} — cost_usd will read 0.00 for it; "
                "token counts remain accurate",
                model,
            )
        self._total += cost
        self._calls += 1
        self._tokens_in += input_tokens
        self._tokens_out += output_tokens

        if self.csv_path is not None:
            with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(
                    [
                        round(time.monotonic() - self._started, 3),
                        self._step if step is None else step,
                        agent,
                        model,
                        input_tokens,
                        output_tokens,
                        f"{cost:.6f}",
                        f"{self._total:.6f}",
                    ]
                )
        return cost

    # -- reporting -----------------------------------------------------------

    @property
    def total_usd(self) -> float:
        return self._total

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def tokens(self) -> tuple[int, int]:
        """(input, output) tokens over the session."""
        return (self._tokens_in, self._tokens_out)

    def summary(self) -> str:
        return (
            f"llm cost ${self._total:.4f} over {self._calls} calls "
            f"({self._tokens_in:,} in / {self._tokens_out:,} out)"
        )

    def log_summary(self) -> None:
        logger.info(self.summary())
        if self._unpriced:
            logger.warning(
                "these models had no price entry, so the total is a LOWER BOUND: {}",
                ", ".join(sorted(self._unpriced)),
            )


__all__ = ["CSV_COLUMNS", "MODEL_PRICING", "CostTracker", "price_of"]
