"""Receipt parsing via vision AI models (Claude, Gemini, Grok)."""

import base64
import json
import os
import re

from backend.config import CATEGORIES, PAYMENT_METHODS, ReceiptData

SYSTEM_PROMPT = """You are a receipt parser for Bulgarian household expenses.
Given a photo of a receipt, extract the following information and return ONLY valid JSON (no markdown, no code fences):

{{
  "date": "DD.MM.YYYY",
  "total_eur": 12.34,
  "category": "one of the allowed categories",
  "payment_method": "one of the allowed payment methods or null",
  "notes": "brief description of main items in Bulgarian, 3-5 words"
}}

Allowed categories: {categories}

Allowed payment methods: {payment_methods}

Rules:
- Date format must be DD.MM.YYYY
- total_eur must be the final total as a number (EUR amount)
- category MUST be exactly one from the allowed list — pick the best match
- payment_method: pick from allowed list if visible on receipt, otherwise null
- notes: short Bulgarian summary of what was purchased
- If the receipt is unclear, make your best guess
"""


def _build_prompt() -> str:
    return SYSTEM_PROMPT.format(
        categories=", ".join(CATEGORIES),
        payment_methods=", ".join(PAYMENT_METHODS),
    )


def _parse_json_response(text: str) -> dict:
    """Extract JSON from model response, handling markdown fences."""
    # Try to find JSON in code fences first
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    # Try raw JSON
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No valid JSON found in response: {text[:200]}")


def _validate_receipt_data(data: dict) -> ReceiptData:
    """Validate and convert parsed JSON to ReceiptData."""
    category = data.get("category", "Разни")
    if category not in CATEGORIES:
        category = "Разни"

    payment = data.get("payment_method")
    if payment and payment not in PAYMENT_METHODS:
        payment = None

    return ReceiptData(
        date=data.get("date", ""),
        total_eur=float(data.get("total_eur", 0)),
        category=category,
        payment_method=payment,
        notes=data.get("notes", ""),
    )


def _parse_with_claude(image_bytes: bytes, mime_type: str) -> dict:
    """Parse receipt using Anthropic Claude API."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    b64 = base64.b64encode(image_bytes).decode()

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _build_prompt()},
                ],
            }
        ],
    )
    return _parse_json_response(message.content[0].text)


def _parse_with_gemini(image_bytes: bytes, mime_type: str) -> dict:
    """Parse receipt using Google Gemini API."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            types.Content(
                parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    types.Part.from_text(text=_build_prompt()),
                ]
            )
        ],
        config=types.GenerateContentConfig(max_output_tokens=512),
    )
    return _parse_json_response(response.text)


def _parse_with_grok(image_bytes: bytes, mime_type: str) -> dict:
    """Parse receipt using xAI Grok API (OpenAI-compatible)."""
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["XAI_API_KEY"],
        base_url="https://api.x.ai/v1",
    )
    b64 = base64.b64encode(image_bytes).decode()

    response = client.chat.completions.create(
        model="grok-2-vision-latest",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64}",
                        },
                    },
                    {"type": "text", "text": _build_prompt()},
                ],
            }
        ],
    )
    return _parse_json_response(response.choices[0].message.content)


_PROVIDERS = {
    "claude": _parse_with_claude,
    "gemini": _parse_with_gemini,
    "grok": _parse_with_grok,
}


def parse_receipt(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    provider: str | None = None,
) -> ReceiptData:
    """Parse a receipt image and return structured data.

    Args:
        image_bytes: Raw image bytes.
        mime_type: Image MIME type (e.g. image/jpeg, image/png).
        provider: Vision provider name. Defaults to VISION_PROVIDER env var.
    """
    if provider is None:
        provider = os.environ.get("VISION_PROVIDER", "claude")

    parse_fn = _PROVIDERS.get(provider)
    if parse_fn is None:
        raise ValueError(
            f"Unknown provider '{provider}'. Choose from: {', '.join(_PROVIDERS)}"
        )

    raw = parse_fn(image_bytes, mime_type)
    return _validate_receipt_data(raw)
