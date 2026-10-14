"""What every provider adapter shares."""

from __future__ import annotations

import json
from dataclasses import dataclass

PROVIDER_NAMES = ("claude", "gemini", "openai", "grok")
MAX_OUTPUT_TOKENS = 16000
TIMEOUT_SECONDS = 120.0
USER_TEXT = (
    "Read this receipt. The photos above are its parts in order, top to bottom "
    "(one photo = the whole receipt)."
)


class ProviderError(Exception):
    """The provider answered, but not with a usable reading (refusal, truncation, …)."""


@dataclass
class ProviderResult:
    data: dict
    input_tokens: int  # all input tokens, cached ones included
    output_tokens: int  # thinking/reasoning included (billed as output)
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class ModelSpec:
    """`provider:model[@effort]`, e.g. `claude:claude-haiku-5-5@low`."""

    provider: str
    model: str
    effort: str | None = None

    @classmethod
    def parse(cls, text: str) -> "ModelSpec":
        provider, sep, rest = text.strip().partition(":")
        model, _, effort = rest.partition("@")
        if not sep or not model:
            raise ValueError(f"model spec must be provider:model[@effort], got {text!r}")
        if provider not in PROVIDER_NAMES:
            raise ValueError(f"unknown provider {provider!r} (one of {', '.join(PROVIDER_NAMES)})")
        return cls(provider, model, effort or None)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}" + (f"@{self.effort}" if self.effort else "")


def media_type(image: bytes) -> str:
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    if image[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/webp"  # what the app stores


def parse_json(text: str | None) -> dict:
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as e:
        raise ProviderError(f"output is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ProviderError("output JSON is not an object")
    return data
