"""Provider adapters build the right structured-output request and parse the response.

Every client is a fake that records the call — no network.
"""

import base64
import io
from types import SimpleNamespace as NS

import pytest

from backend.reading.pricing import cost_usd, price_for
from backend.reading.providers import (
    ModelSpec,
    ProviderError,
    get_provider,
    media_type,
)
from backend.reading.providers.claude import ClaudeProvider
from backend.reading.providers.gemini import GeminiProvider
from backend.reading.providers.openai_compat import GrokProvider, OpenAIProvider

SYSTEM = ["STABLE PROMPT", "Allowed categories:\n- Храна\n"]
SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"],
          "additionalProperties": False}
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 fake"


def webp_image():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="WEBP")
    return buf.getvalue()


class Recorder:
    """A fake SDK method: records kwargs and returns a canned response."""

    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.response


# ---------------------------------------------------------------- spec / helpers

def test_model_spec_parsing():
    spec = ModelSpec.parse("claude:claude-haiku-5-5")
    assert (spec.provider, spec.model, spec.effort) == ("claude", "claude-haiku-5-5", None)
    spec = ModelSpec.parse(" gemini:gemini-x@low ")
    assert (spec.provider, spec.model, spec.effort) == ("gemini", "gemini-x", "low")
    assert str(spec) == "gemini:gemini-x@low"
    with pytest.raises(ValueError):
        ModelSpec.parse("claude-haiku-5-5")
    with pytest.raises(ValueError):
        ModelSpec.parse("mistral:foo")


def test_media_type_sniffing():
    assert media_type(WEBP) == "image/webp"
    assert media_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert media_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert media_type(b"unknown") == "image/webp"


def test_get_provider_returns_adapters():
    assert isinstance(get_provider("claude"), ClaudeProvider)
    assert isinstance(get_provider("gemini"), GeminiProvider)
    assert isinstance(get_provider("openai"), OpenAIProvider)
    assert isinstance(get_provider("grok"), GrokProvider)


# ---------------------------------------------------------------- pricing

def test_claude_prices_and_cost():
    assert price_for("claude-haiku-5-5") == (0.10, 0.50)
    assert price_for("claude-sonnet-5-5") == (2.0, 10.0)
    assert price_for("claude-opus-5-5") == (4.0, 20.0)
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 100_000) == pytest.approx(3.0)


def test_cache_tokens_are_priced():
    # cache writes at 1.25x, reads at 0.1x of the input price
    assert cost_usd("claude-sonnet-5-5", 0, 0, cache_write_tokens=1_000_000,
                    cache_read_tokens=1_000_000) == pytest.approx(2.5 + 0.2)


def test_prices_from_env(monkeypatch):
    monkeypatch.setenv("READING_PRICES", "gemini-x=0.30/2.50, gpt-y=1/8")
    assert price_for("gemini-x") == (0.30, 2.50)
    assert cost_usd("gpt-y", 1_000_000, 1_000_000) == pytest.approx(9.0)


def test_unknown_price_costs_zero_with_warning(caplog):
    assert cost_usd("mystery-model", 1000, 1000) == 0.0
    assert "mystery-model" in caplog.text


# ---------------------------------------------------------------- Claude

def claude_response(text='{"x": 1}', stop_reason="end_turn"):
    return NS(
        stop_reason=stop_reason,
        content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
        usage=NS(input_tokens=1200, output_tokens=300, cache_read_input_tokens=1000,
                 cache_creation_input_tokens=0),
    )


def claude_fake(response):
    create = Recorder(response)
    return NS(messages=NS(create=create)), create


def test_claude_request_uses_output_config_format_and_base64_images():
    client, create = claude_fake(claude_response())
    result = ClaudeProvider(client=client).read(
        [WEBP, b"\xff\xd8\xffjpeg"], SYSTEM, SCHEMA, model="claude-haiku-5-5", effort="low")
    kw = create.kwargs
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA},
                                   "effort": "low"}
    assert "thinking" not in kw and "temperature" not in kw
    assert kw["system"][0] == {"type": "text", "text": "STABLE PROMPT",
                               "cache_control": {"type": "ephemeral"}}
    assert kw["system"][1]["text"].startswith("Allowed categories")
    content = kw["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/webp",
        "data": base64.standard_b64encode(WEBP).decode()}}
    assert content[1]["source"]["media_type"] == "image/jpeg"
    assert content[2]["type"] == "text"
    assert result.data == {"x": 1}
    # input tokens include the cached ones; cost knows they were cache reads
    assert (result.input_tokens, result.output_tokens) == (2200, 300)
    assert result.cache_read_tokens == 1000


def test_claude_without_effort_leaves_the_model_default():
    client, create = claude_fake(claude_response())
    ClaudeProvider(client=client).read([WEBP], SYSTEM, SCHEMA, model="m", effort=None)
    assert "effort" not in create.kwargs["output_config"]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_claude_refusal_or_truncation_is_an_error(stop_reason):
    client, _ = claude_fake(claude_response(stop_reason=stop_reason))
    with pytest.raises(ProviderError, match=stop_reason):
        ClaudeProvider(client=client).read([WEBP], SYSTEM, SCHEMA, model="m", effort=None)


def test_claude_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        ClaudeProvider().read([WEBP], SYSTEM, SCHEMA, model="m", effort=None)


# ---------------------------------------------------------------- Gemini

def gemini_fake(output_text='{"x": 2}', status="completed"):
    create = Recorder(NS(
        status=status, output_text=output_text,
        usage=NS(total_input_tokens=1500, total_output_tokens=200, total_thought_tokens=100)))
    return NS(interactions=NS(create=create)), create


def test_gemini_request_uses_interactions_response_format():
    client, create = gemini_fake()
    result = GeminiProvider(client=client).read([WEBP], SYSTEM, SCHEMA, model="gemini-x",
                                                effort="low")
    kw = create.kwargs
    assert kw["model"] == "gemini-x"
    assert kw["response_format"] == {"type": "text", "mime_type": "application/json",
                                     "schema": SCHEMA}
    assert kw["system_instruction"] == "STABLE PROMPT\n\nAllowed categories:\n- Храна\n"
    assert kw["input"][0] == {"type": "image", "mime_type": "image/webp",
                              "data": base64.b64encode(WEBP).decode()}
    assert kw["input"][-1]["type"] == "text"
    assert kw["generation_config"]["thinking_level"] == "low"
    assert kw["store"] is False
    assert result.data == {"x": 2}
    # thinking is billed as output
    assert (result.input_tokens, result.output_tokens) == (1500, 300)


def test_gemini_incomplete_is_an_error():
    client, _ = gemini_fake(status="incomplete")
    with pytest.raises(ProviderError):
        GeminiProvider(client=client).read([WEBP], SYSTEM, SCHEMA, model="g", effort=None)


# ---------------------------------------------------------------- OpenAI / Grok

def openai_fake(output_text='{"x": 3}', status="completed", refusal=False):
    content = [NS(type="refusal", refusal="no")] if refusal else [
        NS(type="output_text", text=output_text)]
    create = Recorder(NS(
        status=status, output_text=output_text,
        output=[NS(type="message", content=content)],
        incomplete_details=NS(reason="max_output_tokens") if status == "incomplete" else None,
        usage=NS(input_tokens=1800, output_tokens=400)))
    return NS(responses=NS(create=create)), create


def test_openai_request_uses_strict_json_schema_text_format():
    client, create = openai_fake()
    result = OpenAIProvider(client=client).read([WEBP], SYSTEM, SCHEMA, model="gpt-x",
                                                effort="low")
    kw = create.kwargs
    assert kw["model"] == "gpt-x"
    assert kw["text"] == {"format": {"type": "json_schema", "name": "receipt",
                                     "schema": SCHEMA, "strict": True}}
    assert kw["instructions"] == "STABLE PROMPT\n\nAllowed categories:\n- Храна\n"
    assert kw["reasoning"] == {"effort": "low"}
    assert kw["store"] is False
    image = kw["input"][0]["content"][0]
    assert image == {"type": "input_image", "detail": "high",
                     "image_url": "data:image/webp;base64," + base64.b64encode(WEBP).decode()}
    assert kw["input"][0]["content"][-1]["type"] == "input_text"
    assert result.data == {"x": 3}
    assert (result.input_tokens, result.output_tokens) == (1800, 400)


def test_openai_refusal_and_incomplete_are_errors():
    client, _ = openai_fake(refusal=True)
    with pytest.raises(ProviderError, match="refus"):
        OpenAIProvider(client=client).read([WEBP], SYSTEM, SCHEMA, model="g", effort=None)
    client, _ = openai_fake(status="incomplete")
    with pytest.raises(ProviderError, match="max_output_tokens"):
        OpenAIProvider(client=client).read([WEBP], SYSTEM, SCHEMA, model="g", effort=None)


def test_grok_sends_jpeg_because_xai_takes_no_webp():
    client, create = openai_fake()
    GrokProvider(client=client).read([webp_image()], SYSTEM, SCHEMA, model="grok-x",
                                     effort=None)
    kw = create.kwargs
    assert kw["model"] == "grok-x"
    assert "reasoning" not in kw
    assert kw["input"][0]["content"][0]["image_url"].startswith("data:image/jpeg;base64,")


def test_grok_client_points_at_xai(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k")
    client = GrokProvider()._make_client()
    assert str(client.base_url).rstrip("/") == "https://api.x.ai/v1"


def test_invalid_json_is_a_provider_error():
    client, _ = openai_fake(output_text="not json")
    with pytest.raises(ProviderError, match="JSON"):
        OpenAIProvider(client=client).read([WEBP], SYSTEM, SCHEMA, model="g", effort=None)
