"""Tests for toolkit/llm_providers.py — LLM provider layer (Round 6, PR A).

Offline only: every provider here is exercised through FakeProvider
or through a stub transport that returns canned HTTP responses. No
real key exists anywhere in this file — the only key-shaped values
are placeholders containing "test"/"fake", which the secret gate
recognises as fixtures. No network is used.
"""

import json
import logging

import pytest

from toolkit.llm_providers import (
    AnthropicProvider,
    FakeProvider,
    GeminiProvider,
    LLMCapError,
    LLMConfigError,
    LLMProviderError,
    LLMSchemaError,
    OllamaProvider,
    OpenAICompatibleProvider,
    estimate_tokens,
    get_provider,
    provider_from_env,
    redact_text,
    validate_json,
)

SCHEMA = {
    "type": "object",
    "required": ["direction", "confidence"],
    "properties": {
        "direction": {"type": "string", "enum": ["up", "flat", "down"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": False,
}

GOOD_REPLY = '{"direction": "up", "confidence": 0.6}'

# Placeholder key: contains "test", so the secret gate treats it as a
# fixture, never a credential.
TEST_KEY = "test-key-0123456789abcdef"


def _stub_transport(calls, status=200, payload=None, raise_error=None):
    """A transport that records calls and returns one canned response."""

    def transport(method, url, headers, body):
        calls.append({"method": method, "url": url, "headers": dict(headers),
                      "body": body})
        if raise_error is not None:
            raise raise_error
        return status, json.dumps(payload).encode("utf-8")

    return transport


# --- fake provider: happy path and schema repair ---------------------------

def test_fake_provider_returns_valid_json():
    provider = FakeProvider(script=[GOOD_REPLY])
    result = provider.complete("direction for DEMO?", schema=SCHEMA)
    assert result.parsed == {"direction": "up", "confidence": 0.6}
    assert result.attempts == 1
    assert result.provider == "fake"
    assert result.tokens_used == 30  # fixed (10 input, 20 output)
    assert result.cost_usd_estimate == 0.0


def test_fake_provider_without_schema_returns_text():
    provider = FakeProvider(script=["plain research note"])
    result = provider.complete("summarise")
    assert result.text == "plain research note"
    assert result.parsed is None


def test_schema_failure_then_repair_succeeds():
    provider = FakeProvider(script=["not json at all", GOOD_REPLY])
    result = provider.complete("direction?", schema=SCHEMA)
    assert result.parsed == {"direction": "up", "confidence": 0.6}
    assert result.attempts == 2
    assert provider.calls == 2


def test_schema_failure_exhausts_attempts():
    provider = FakeProvider(script=['{"direction": "sideways"}'],
                            max_attempts=2)
    with pytest.raises(LLMSchemaError, match="did not match schema"):
        provider.complete("direction?", schema=SCHEMA)
    assert provider.calls == 2


def test_schema_error_names_the_bad_field():
    provider = FakeProvider(script=['{"direction": "up"}'], max_attempts=1)
    with pytest.raises(LLMSchemaError, match="confidence"):
        provider.complete("direction?", schema=SCHEMA)


# --- caps --------------------------------------------------------------------

def test_token_cap_exceeded_before_second_call():
    provider = FakeProvider(script=[GOOD_REPLY, GOOD_REPLY],
                            tokens_per_call=(3000, 3000),
                            max_tokens_per_run=4000)
    with pytest.raises(LLMCapError, match="token cap exceeded"):
        provider.complete("direction?", schema=SCHEMA)
    assert provider.calls == 1


def test_cost_cap_exceeded():
    provider = FakeProvider(script=[GOOD_REPLY], rates=(1.0, 1.0),
                            max_cost_usd_per_run=0.01)
    # 30 tokens at $1.00/1k = $0.03 estimated, over the $0.01 cap.
    with pytest.raises(LLMCapError, match="cost cap exceeded"):
        provider.complete("direction?", schema=SCHEMA)


def test_zero_token_cap_is_rejected_at_construction():
    with pytest.raises(LLMConfigError, match="max_tokens_per_run"):
        FakeProvider(max_tokens_per_run=0)


# --- retries -----------------------------------------------------------------

def test_retry_limit_counts_attempts():
    failures = [LLMProviderError("boom") for _ in range(3)]
    provider = FakeProvider(script=failures, max_attempts=3)
    with pytest.raises(LLMProviderError, match="boom"):
        provider.complete("direction?")
    assert provider.calls == 3


def test_transient_failure_then_success():
    provider = FakeProvider(
        script=[LLMProviderError("boom"), GOOD_REPLY], max_attempts=3)
    result = provider.complete("direction?", schema=SCHEMA)
    assert result.parsed["direction"] == "up"
    assert result.attempts == 2


def test_http_500_retries_then_succeeds():
    calls = []
    responses = [(500, b"error"), (200, json.dumps(
        {"message": {"content": GOOD_REPLY}}).encode("utf-8"))]

    def flaky(method, url, headers, body):
        calls.append(url)
        return responses.pop(0)

    provider = OllamaProvider(transport=flaky, max_attempts=3)
    result = provider.complete("direction?", schema=SCHEMA)
    assert result.parsed["direction"] == "up"
    assert len(calls) == 2


# --- key redaction -----------------------------------------------------------

def test_key_never_in_exception_or_logs(caplog):
    calls = []

    def leaking_transport(method, url, headers, body):
        calls.append(url)
        raise LLMProviderError(f"connection failed for key {TEST_KEY}")

    provider = OpenAICompatibleProvider(api_key=TEST_KEY,
                                        transport=leaking_transport,
                                        max_attempts=1)
    with caplog.at_level(logging.WARNING,
                          logger="toolkit.llm_providers"), \
            pytest.raises(LLMProviderError) as excinfo:
        provider.complete("direction?")
    assert TEST_KEY not in str(excinfo.value)
    assert TEST_KEY not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_redact_text_covers_key_shapes():
    assert redact_text("key is test-key-0123456789abcdef",
                       secrets=(TEST_KEY,)) == "key is [REDACTED]"
    assert "sk-" not in redact_text(
        "token sk-testkey123456789")
    assert redact_text("Authorization: Bearer abcdefgh12345678") == \
        "Authorization: Bearer [REDACTED]"
    assert redact_text("x-api-key: abcdefgh12345678") == \
        "x-api-key: [REDACTED]"
    assert redact_text("nothing secret here") == "nothing secret here"


def test_repr_never_shows_key():
    provider = OpenAICompatibleProvider(api_key=TEST_KEY)
    assert TEST_KEY not in repr(provider)
    assert "key_set=True" in repr(provider)


def test_missing_key_raises_config_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        OpenAICompatibleProvider()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="GEMINI_API_KEY"):
        GeminiProvider()


def test_empty_prompt_rejected():
    with pytest.raises(LLMConfigError, match="non-empty string"):
        FakeProvider().complete("   ")


# --- HTTP providers: canned responses, no network ----------------------------

def test_openai_parses_canned_response():
    calls = []
    transport = _stub_transport(
        calls, payload={"choices": [{"message": {"content": GOOD_REPLY}}]})
    provider = OpenAICompatibleProvider(api_key=TEST_KEY, transport=transport)
    result = provider.complete("direction?", schema=SCHEMA)
    assert result.parsed["direction"] == "up"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {TEST_KEY}"
    assert calls[0]["url"] == "https://api.openai.com/v1/chat/completions"


def test_anthropic_parses_canned_response():
    calls = []
    transport = _stub_transport(
        calls, payload={"content": [{"text": GOOD_REPLY}]})
    provider = AnthropicProvider(api_key=TEST_KEY, transport=transport)
    result = provider.complete("direction?", schema=SCHEMA)
    assert result.parsed["direction"] == "up"
    assert calls[0]["headers"]["x-api-key"] == TEST_KEY
    assert calls[0]["headers"]["anthropic-version"] == "2023-06-01"
    assert json.loads(calls[0]["body"])["max_tokens"] == 1024


def test_gemini_parses_canned_response_and_key_not_in_url():
    calls = []
    transport = _stub_transport(
        calls, payload={"candidates": [{"content": {"parts": [
            {"text": GOOD_REPLY}]}}]})
    provider = GeminiProvider(api_key=TEST_KEY, transport=transport)
    result = provider.complete("direction?", schema=SCHEMA)
    assert result.parsed["direction"] == "up"
    assert calls[0]["headers"]["x-goog-api-key"] == TEST_KEY
    assert "key=" not in calls[0]["url"]


def test_ollama_parses_canned_response_without_key():
    calls = []
    transport = _stub_transport(
        calls, payload={"message": {"content": GOOD_REPLY}})
    provider = OllamaProvider(transport=transport)
    result = provider.complete("direction?", schema=SCHEMA)
    assert result.parsed["direction"] == "up"
    assert "Authorization" not in calls[0]["headers"]
    assert calls[0]["url"] == "http://localhost:11434/api/chat"


def test_openai_401_is_not_retried():
    calls = []
    transport = _stub_transport(calls, status=401, payload={
        "error": {"message": "bad key"}})
    provider = OpenAICompatibleProvider(api_key=TEST_KEY,
                                        transport=transport, max_attempts=3)
    with pytest.raises(LLMProviderError, match="status 401"):
        provider.complete("direction?")
    assert len(calls) == 1  # non-retryable: no attempt is wasted


# --- factory -----------------------------------------------------------------

def test_get_provider_names():
    assert isinstance(get_provider("fake"), FakeProvider)
    assert isinstance(get_provider("ollama"), OllamaProvider)
    assert isinstance(
        get_provider("openai", api_key=TEST_KEY), OpenAICompatibleProvider)
    assert isinstance(
        get_provider("anthropic", api_key=TEST_KEY), AnthropicProvider)
    assert isinstance(
        get_provider("gemini", api_key=TEST_KEY), GeminiProvider)
    with pytest.raises(LLMConfigError, match="unknown provider"):
        get_provider("no-such-provider")


def test_provider_from_env_prefers_key_then_ollama(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert isinstance(provider_from_env(), OllamaProvider)
    monkeypatch.setenv("GEMINI_API_KEY", TEST_KEY)
    assert isinstance(provider_from_env(), GeminiProvider)
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    assert isinstance(provider_from_env(), AnthropicProvider)
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    assert isinstance(provider_from_env(), OpenAICompatibleProvider)


# --- validator and token estimate --------------------------------------------

def test_validate_json_covers_types_and_bounds():
    assert validate_json({"a": 1}, SCHEMA) == [
        "$: missing required field 'direction'",
        "$: missing required field 'confidence'",
        "$: additional property 'a' not allowed",
    ]
    assert validate_json({"direction": "sideways", "confidence": 0.5},
                         SCHEMA) == [
        ("$.direction: value 'sideways' not in enum "
         "['up', 'flat', 'down']"),
    ]
    assert validate_json({"direction": "up", "confidence": 2,
                          "extra": 1}, SCHEMA) == [
        "$.confidence: 2 above maximum 1",
        "$: additional property 'extra' not allowed",
    ]
    assert validate_json("not an object", SCHEMA) == [
        "$: expected type object, got str",
    ]
    assert validate_json({"direction": "up", "confidence": 0.5},
                         SCHEMA) == []
    assert validate_json([1, 2], {"type": "array", "minItems": 3,
                                  "items": {"type": "integer"}}) == [
        "$: array shorter than minItems 3",
    ]


def test_estimate_tokens_is_deterministic():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("x" * 400) == 100
