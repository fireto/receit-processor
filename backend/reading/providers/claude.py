"""Claude via the official `anthropic` SDK: Messages API with `output_config.format`
(structured outputs), base64 images, optional `output_config.effort`.

Thinking is left at the model default (adaptive on the 5.5 models); depth is set with
effort. Server-side refusal fallbacks are not used: the ladder and the backup provider
handle a failed call, and each attempt must name the model that really answered.
"""

from __future__ import annotations

import base64
import os

from backend.reading.providers.base import (
    MAX_OUTPUT_TOKENS,
    TIMEOUT_SECONDS,
    USER_TEXT,
    ProviderError,
    ProviderResult,
    media_type,
    parse_json,
)


class ClaudeProvider:
    name = "claude"
    key_env = "ANTHROPIC_API_KEY"

    def __init__(self, client=None):
        self._client = client

    def _make_client(self):
        if not os.environ.get(self.key_env):
            raise ProviderError(f"{self.key_env} not set")
        import anthropic

        return anthropic.Anthropic(api_key=os.environ[self.key_env], timeout=TIMEOUT_SECONDS,
                                   max_retries=1)

    def read(self, images: list[bytes], system: list[str], schema: dict, *, model: str,
             effort: str | None) -> ProviderResult:
        client = self._client or self._make_client()
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type(img),
                                         "data": base64.standard_b64encode(img).decode()}}
            for img in images
        ]
        content.append({"type": "text", "text": USER_TEXT})
        output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[
                {"type": "text", "text": system[0], "cache_control": {"type": "ephemeral"}},
                *({"type": "text", "text": block} for block in system[1:]),
            ],
            messages=[{"role": "user", "content": content}],
            output_config=output_config,
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise ProviderError(f"stop_reason {response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), None)
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return ProviderResult(
            data=parse_json(text),
            input_tokens=usage.input_tokens + cache_read + cache_write,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
