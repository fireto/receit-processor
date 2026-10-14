"""Receipt reading: vision models with structured output, self-check and a model ladder."""

from backend.reading.models import (
    Attempt,
    Discount,
    Line,
    Reading,
    ReadOutcome,
    SelfCheck,
)

__all__ = ["Attempt", "Discount", "Line", "Reading", "ReadOutcome", "SelfCheck"]
