"""Gemini via the official `google-genai` SDK: the Interactions API
(`client.interactions.create`) with `response_format` = JSON + schema, inline base64
images, `generation_config.thinking_level` as the effort.
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


class GeminiProvider:
    name = "gemini"
    key_env = "GOOGLE_API_KEY"

    def __init__(self, client=None):
        self._client = client

    def _make_client(self):
        if not os.environ.get(self.key_env):
            raise ProviderError(f"{self.key_env} not set")
        from google import genai

        return genai.Client(api_key=os.environ[self.key_env])

    def read(self, images: list[bytes], system: list[str], schema: dict, *, model: str,
             effort: str | None) -> ProviderResult:
        client = self._client or self._make_client()
        parts = [
            {"type": "image", "mime_type": media_type(img),
             "data": base64.b64encode(img).decode()}
            for img in images
        ]
        parts.append({"type": "text", "text": USER_TEXT})
        generation_config: dict = {"max_output_tokens": MAX_OUTPUT_TOKENS}
        if effort:
            generation_config["thinking_level"] = effort
        interaction = client.interactions.create(
            model=model,
            system_instruction="\n\n".join(system),
            input=parts,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema},
            generation_config=generation_config,
            store=False,
            timeout=TIMEOUT_SECONDS,
        )
        status = getattr(interaction, "status", None)
        if status not in (None, "completed"):
            raise ProviderError(f"interaction status {status}")
        usage = interaction.usage
        return ProviderResult(
            data=parse_json(interaction.output_text),
            input_tokens=usage.total_input_tokens or 0,
            output_tokens=(usage.total_output_tokens or 0) + (usage.total_thought_tokens or 0),
        )
