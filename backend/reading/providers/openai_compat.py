"""OpenAI and xAI Grok via the official `openai` SDK: the Responses API with
`text.format` = strict `json_schema`, images as base64 data URLs (`input_image`),
`reasoning.effort` as the effort. xAI's API is OpenAI-compatible at https://api.x.ai/v1
and lists only JPEG/PNG input, so Grok gets the photos re-encoded as JPEG.
"""

from __future__ import annotations

import base64
import io
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


class OpenAIProvider:
    name = "openai"
    key_env = "OPENAI_API_KEY"
    base_url: str | None = None

    def __init__(self, client=None):
        self._client = client

    def _make_client(self):
        if not os.environ.get(self.key_env):
            raise ProviderError(f"{self.key_env} not set")
        from openai import OpenAI

        return OpenAI(api_key=os.environ[self.key_env], base_url=self.base_url,
                      timeout=TIMEOUT_SECONDS, max_retries=1)

    def _image(self, img: bytes) -> tuple[str, bytes]:
        return media_type(img), img

    def read(self, images: list[bytes], system: list[str], schema: dict, *, model: str,
             effort: str | None) -> ProviderResult:
        client = self._client or self._make_client()
        content = []
        for img in images:
            mime, data = self._image(img)
            content.append({"type": "input_image", "detail": "high",
                            "image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}"})
        content.append({"type": "input_text", "text": USER_TEXT})
        kwargs: dict = dict(
            model=model,
            instructions="\n\n".join(system),
            input=[{"role": "user", "content": content}],
            text={"format": {"type": "json_schema", "name": "receipt", "schema": schema,
                             "strict": True}},
            max_output_tokens=MAX_OUTPUT_TOKENS,
            store=False,
        )
        if effort:
            kwargs["reasoning"] = {"effort": effort}
        response = client.responses.create(**kwargs)
        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", None)
            raise ProviderError(f"incomplete response: {reason}")
        for item in response.output or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    raise ProviderError(f"refusal: {part.refusal}")
        usage = response.usage
        return ProviderResult(data=parse_json(response.output_text),
                              input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


class GrokProvider(OpenAIProvider):
    name = "grok"
    key_env = "XAI_API_KEY"
    base_url = "https://api.x.ai/v1"

    def _image(self, img: bytes) -> tuple[str, bytes]:
        mime = media_type(img)
        if mime in ("image/jpeg", "image/png"):
            return mime, img
        from PIL import Image

        buf = io.BytesIO()
        Image.open(io.BytesIO(img)).convert("RGB").save(buf, format="JPEG", quality=90)
        return "image/jpeg", buf.getvalue()
