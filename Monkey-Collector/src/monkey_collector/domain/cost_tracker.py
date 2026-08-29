"""Token cost tracking for LLM API calls.

Logs per-call token usage and cost to a CSV file,
enabling cost analysis per step and per agent.

CSV format:
    timestamp_sec,step,agent,model,input_tokens,output_tokens,cost_usd,total
"""

import contextlib
import csv
import os
import time

from loguru import logger

# Pricing per 1M tokens (USD)
MODEL_PRICING = {
    # OpenRouter (current default provider).
    # Looked up directly against https://openrouter.ai/api/v1/models on
    # 2026-08-29 — re-verify there if these look stale, slug/price may change.
    "qwen/qwen3.8-flash": {"input": 0.15, "output": 0.47},
    # Kept for historical cost.csv compatibility (rows already written under
    # this slug from earlier runs) — no longer DEFAULT_MODEL. Previously
    # recorded here as 0.40/1.20, which was wrong; corrected 2026-08-29 from
    # the same lookup as the flash row above.
    "qwen/qwen3.7-plus": {"input": 0.32, "output": 1.28},
    # Unknown models fall through to 0.0 in _calc_cost. That 0.0 means "no
    # pricing entry for this model", NOT "this call was free" — _calc_cost
    # logs a WARNING every time this fallback fires, so a budget silently
    # reading as $0 does not go unnoticed.
    # OpenAI Chat (legacy — kept for historical cost.csv compatibility)
    "gpt-5-nano": {"input": 0.10, "output": 0.40},
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


class CostTracker:
    """Tracks and persists per-call token usage and cost to CSV."""

    CSV_COLUMNS = [
        "timestamp_sec", "step", "agent",
        "model", "input_tokens", "output_tokens", "cost_usd", "total",
    ]

    def __init__(self):
        self.csv_path: str = ""
        self.start_time: float = 0.0
        self._total: float = 0.0
        self._initialized = False

    def initialize(self, session_dir: str) -> None:
        """Create CSV file with header.

        Resets internal state so the tracker can be reused across sessions.
        """
        self.csv_path = os.path.join(session_dir, "cost.csv")
        self.start_time = time.time()
        self._total = 0.0

        with open(self.csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS)
            writer.writeheader()
        self._initialized = True
        logger.info(f"Cost tracker initialized: csv={self.csv_path}")

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        step: int,
        agent: str = "text_generator",
    ) -> dict:
        """Record a single API call's token usage and cost.

        Returns:
            dict with cost entry data.
        """
        if not self._initialized:
            return {}

        elapsed = round(time.time() - self.start_time, 2)
        cost = self._calc_cost(model, input_tokens, output_tokens)
        self._total += cost

        entry = {
            "timestamp_sec": elapsed,
            "step": step,
            "agent": agent,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "total": round(self._total, 8),
        }

        with open(self.csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS)
            writer.writerow(entry)

        return entry

    def resume(self, session_dir: str) -> None:
        """Resume from existing cost.csv.

        Rebuilds accumulated total from CSV and appends new records.
        """
        self.csv_path = os.path.join(session_dir, "cost.csv")
        self.start_time = time.time()
        self._total = 0.0

        if os.path.exists(self.csv_path):
            with open(self.csv_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    with contextlib.suppress(ValueError):
                        self._total = float(row.get("total", 0))

        self._initialized = True
        logger.info(
            f"Cost tracker resumed: previous total=${self._total:.6f}"
        )

    def get_total_cost(self) -> float:
        """Return accumulated total cost in USD."""
        return self._total

    @staticmethod
    def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD based on model pricing.

        A *model* absent from :data:`MODEL_PRICING` costs $0.00 here — but that
        0.0 means "unknown model", not "free call". Silently recording $0 for
        an unpriced model would let a budget look healthy while actually not
        being tracked at all, so this logs a WARNING every time the fallback
        fires (not just once) so it shows up next to the cost.csv row it produced.
        """
        pricing = MODEL_PRICING.get(model)
        if pricing is None:
            logger.warning(
                f"No MODEL_PRICING entry for {model!r} — cost for this call is "
                "being recorded as $0.00, which means UNKNOWN cost, not a free "
                "call. Add this model to MODEL_PRICING to track it for real."
            )
            pricing = {"input": 0.0, "output": 0.0}
        return (
            input_tokens * pricing["input"] + output_tokens * pricing["output"]
        ) / 1_000_000
