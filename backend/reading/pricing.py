"""Model prices (USD per 1M input / output tokens) and the cost of one call.

Claude prices are built in; any other model gets its price from READING_PRICES, e.g.
`READING_PRICES="gemini-x=0.30/2.50,gpt-y=1.25/10"` (also overrides the built-ins).
An unknown price costs 0 and logs a warning.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

BUILTIN_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
CACHE_WRITE_FACTOR = 1.25  # Claude: 5-minute cache writes
CACHE_READ_FACTOR = 0.10


def _env_prices() -> dict[str, tuple[float, float]]:
    prices = {}
    for item in os.environ.get("READING_PRICES", "").split(","):
        if "=" not in item:
            continue
        model, _, pair = item.partition("=")
        try:
            inp, out = (float(x) for x in pair.split("/"))
        except ValueError:
            logger.warning("READING_PRICES: cannot parse %r", item)
            continue
        prices[model.strip()] = (inp, out)
    return prices


def price_for(model: str) -> tuple[float, float] | None:
    return {**BUILTIN_PRICES, **_env_prices()}.get(model)


def cost_usd(model: str, input_tokens: int, output_tokens: int, *,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Cost of one call. input_tokens counts every input token, cached ones included."""
    price = price_for(model)
    if price is None:
        logger.warning("no price for model %s (set READING_PRICES); counting 0", model)
        return 0.0
    inp, out = price
    uncached = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
    weighted = (uncached + cache_write_tokens * CACHE_WRITE_FACTOR
                + cache_read_tokens * CACHE_READ_FACTOR)
    return (weighted * inp + output_tokens * out) / 1_000_000
