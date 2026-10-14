"""Provider adapters: same interface, one per vendor SDK.

`read(images, system, schema, *, model, effort) -> ProviderResult`; each takes an
optional injected SDK client (tests pass fakes).
"""

from backend.reading.providers.base import (
    PROVIDER_NAMES,
    ModelSpec,
    ProviderError,
    ProviderResult,
    media_type,
)
from backend.reading.providers.claude import ClaudeProvider
from backend.reading.providers.gemini import GeminiProvider
from backend.reading.providers.openai_compat import GrokProvider, OpenAIProvider

PROVIDERS = {
    "claude": ClaudeProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "grok": GrokProvider,
}


def get_provider(name: str, client=None):
    return PROVIDERS[name](client=client)


def key_env(name: str) -> str:
    return PROVIDERS[name].key_env


__all__ = ["PROVIDER_NAMES", "PROVIDERS", "ModelSpec", "ProviderError", "ProviderResult",
           "get_provider", "key_env", "media_type"]
