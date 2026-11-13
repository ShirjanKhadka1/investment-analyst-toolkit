"""LLM provider layer — standard library only, research only.

One small adapter per provider, all speaking HTTP through ``urllib``:

- ``OllamaProvider`` — local, free, no key (default ``localhost:11434``).
- ``OpenAICompatibleProvider`` — any OpenAI-compatible ``/chat/completions``
  endpoint; key from the ``OPENAI_API_KEY`` environment variable (or an
  explicit argument). ``OPENAI_BASE_URL`` overrides the endpoint.
- ``AnthropicProvider`` — ``api.anthropic.com/v1/messages``; key from
  ``ANTHROPIC_API_KEY``.
- ``GeminiProvider`` — ``generativelanguage.googleapis.com``; key from
  ``GEMINI_API_KEY``, sent as the ``x-goog-api-key`` header (never in a
  URL, so it cannot leak into a logged request line).
- ``FakeProvider`` — deterministic, offline, for tests and CI. It plays
  back a scripted list of responses (or raises scripted errors) and
  charges a fixed token count per call, so caps and retries are testable
  without a network or a key.

Design (the portable idea, learned from TradingAgents' multi-provider
client factory — Apache-2.0, ideas only, no code copied): one factory,
one result shape, and the caller never sees a vendor payload. The
factory itself is deliberately thin: no LangChain, no model catalogue
that churns monthly — the caller names the model.

Hard limits, enforced on every call:

- Token cap: estimated tokens per run (``len(text) // 4``, ceiling 1 —
  an estimate, not a provider count) must stay under
  ``max_tokens_per_run`` (default 4000). Exceeding it raises
  ``LLMCapError`` before the next request is sent.
- Cost cap: estimated cost from the provider's configured per-1k-token
  rates must stay under ``max_cost_usd_per_run`` (default $0.50).
  The default rates are conservative cap defaults, NOT price quotes —
  they are set above typical list prices so the cap binds early.
  Set the exact rates from the provider's pricing page before real
  use; the estimate is reported on every result so it can be checked.
- Bounded attempts: one ``max_attempts`` (default 3) bounds the whole
  loop — a transport failure and a schema failure both consume an
  attempt. After the last attempt the error is raised, never retried
  silently.

Schema-validated JSON: ``complete(..., schema={...})`` parses the reply
as JSON and validates it against a small built-in subset of JSON Schema
(``type``, ``required``, ``properties``, ``additionalProperties``,
``items``, ``enum``, ``minimum``/``maximum``, ``minLength``/``maxLength``,
``minItems``). A failed reply triggers a repair prompt carrying the
validation errors, bounded by the same attempt cap.

Keys: read from the environment (or an explicit argument) at
construction, never logged, never stored in a result, and never
included in an exception message or a log record. Every string that
reaches a log call or an exception passes through ``redact_text``,
which replaces any known key value and any ``sk-…`` / ``Bearer …`` /
``x-api-key: …`` shaped token with ``[REDACTED]``.

Nothing in this module executes an order, places a trade, or makes a
recommendation. It returns text (or validated JSON) for a research
caller to use; the caller owns every claim made from it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS_PER_RUN = 4000
DEFAULT_MAX_COST_USD_PER_RUN = 0.50
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT_SECONDS = 60.0

# Per-1k-token rates (input, output), in USD. CONSERVATIVE CAP DEFAULTS,
# not price quotes: set above typical list prices so the cost cap binds
# early. Override with the provider's published rates before real use.
DEFAULT_RATES = {
    "ollama": (0.0, 0.0),
    "openai-compatible": (0.005, 0.015),
    "anthropic": (0.015, 0.075),
    "gemini": (0.0025, 0.010),
    "fake": (0.0, 0.0),
}

_REDACT_PATTERNS = (
    # 8+ characters, not 20: logs should over-redact, not under-redact.
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(x-api-key\s*:\s*)[^\s\",}]+"),
    re.compile(r"(?i)(api[_-]?key\s*=\s*)[^\s&\",}]+"),
)


class LLMError(Exception):
    """Base class for every error raised by this module."""


class LLMConfigError(LLMError):
    """Bad configuration: unknown provider, missing key, bad arguments."""


class LLMProviderError(LLMError):
    """The provider call failed (transport error or error status).

    ``retryable`` is False for errors that will not change on retry
    (e.g. HTTP 401/403/404): the caller must not burn attempts on
    them. Transport failures and 429/5xx are retryable.
    """

    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class LLMSchemaError(LLMError):
    """The reply was not valid JSON matching the schema after all attempts."""


class LLMCapError(LLMError):
    """The run would exceed its token or cost cap."""


def estimate_tokens(text):
    """Deterministic token estimate: one token per four characters.

    This is an estimate, not a provider count — it exists so the cap
    can be enforced before a request is sent, not to bill anyone.
    """
    return max(1, math.ceil(len(text) / 4))


def redact_text(text, secrets=()):
    """Replace key material in *text* with ``[REDACTED]``.

    Replaces every value in *secrets* (length 4 or more) plus any
    ``sk-…`` / ``Bearer …`` / ``x-api-key: …`` shaped token. Safe to
    call on any string, including one that contains no secrets.
    """
    redacted = str(text)
    for secret in secrets:
        if secret and len(str(secret)) >= 4:
            redacted = redacted.replace(str(secret), "[REDACTED]")
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub(_redact_match, redacted)
    return redacted


def _redact_match(match):
    """Replacement for a redaction-pattern match: keep a label, drop value."""
    text = match.group(0)
    if ":" in text and text.lower().startswith("x-api-key"):
        return "x-api-key: [REDACTED]"
    if "=" in text:
        return text.split("=", 1)[0] + "=[REDACTED]"
    if text.startswith("Bearer"):
        return "Bearer [REDACTED]"
    return "[REDACTED]"


def validate_json(data, schema):
    """Validate *data* against a small JSON Schema subset.

    Returns a list of error strings (empty means valid). Supported
    keywords: ``type``, ``required``, ``properties``,
    ``additionalProperties``, ``items``, ``enum``, ``minimum``,
    ``maximum``, ``minLength``, ``maxLength``, ``minItems``. Unknown
    keywords are ignored. This is a validator, not a full JSON Schema
    implementation — keep schemas to the keywords above.
    """
    errors = []
    _validate(data, schema, "$", errors)
    return errors


def _validate(data, schema, path, errors):
    if not isinstance(schema, dict):
        return
    expected = schema.get("type")
    if expected is not None and not _matches_type(data, expected):
        errors.append(f"{path}: expected type {expected}, "
                      f"got {type(data).__name__}")
        return
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: value {data!r} not in enum "
                      f"{schema['enum']!r}")
    if isinstance(data, dict):
        for field in schema.get("required", []):
            if field not in data:
                errors.append(f"{path}: missing required field {field!r}")
        properties = schema.get("properties", {})
        for field, subschema in properties.items():
            if field in data:
                _validate(data[field], subschema, f"{path}.{field}", errors)
        if schema.get("additionalProperties") is False:
            for field in data:
                if field not in properties:
                    errors.append(f"{path}: additional property "
                                  f"{field!r} not allowed")
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            errors.append(f"{path}: {data!r} below minimum "
                          f"{schema['minimum']!r}")
        if "maximum" in schema and data > schema["maximum"]:
            errors.append(f"{path}: {data!r} above maximum "
                          f"{schema['maximum']!r}")
    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            errors.append(f"{path}: string shorter than minLength "
                          f"{schema['minLength']}")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            errors.append(f"{path}: string longer than maxLength "
                          f"{schema['maxLength']}")
    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            errors.append(f"{path}: array shorter than minItems "
                          f"{schema['minItems']}")
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for index, item in enumerate(data):
                _validate(item, items_schema, f"{path}[{index}]", errors)


def _matches_type(data, expected):
    if expected == "object":
        return isinstance(data, dict)
    if expected == "array":
        return isinstance(data, list)
    if expected == "string":
        return isinstance(data, str)
    if expected == "boolean":
        return isinstance(data, bool)
    if expected == "integer":
        return isinstance(data, int) and not isinstance(data, bool)
    if expected == "number":
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    if expected == "null":
        return data is None
    return True


class LLMResult:
    """One completed call: text, parsed JSON (if a schema was given),
    estimated tokens and cost, and how many attempts it took."""

    def __init__(self, text, parsed, tokens_used, cost_usd_estimate,
                 provider, model, attempts):
        self.text = text
        self.parsed = parsed
        self.tokens_used = tokens_used
        self.cost_usd_estimate = cost_usd_estimate
        self.provider = provider
        self.model = model
        self.attempts = attempts

    def __repr__(self):  # never prints text or keys, only counters
        return (f"LLMResult(provider={self.provider!r}, model={self.model!r}, "
                f"tokens_used={self.tokens_used}, "
                f"cost_usd_estimate={self.cost_usd_estimate:.6f}, "
                f"attempts={self.attempts})")


def _default_transport(method, url, headers, body):
    """One HTTP call via stdlib urllib. Returns ``(status, body_bytes)``.

    Transport errors become LLMProviderError; HTTP error statuses are
    returned, not raised, so the caller can decide what is retryable.
    """
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(
                request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise LLMProviderError(
            f"transport error calling provider: "
            f"{redact_text(str(exc))}") from exc


class BaseProvider:
    """Shared cap, retry-and-repair and redaction logic for all providers.

    Subclasses implement ``_complete_once(messages)`` (one HTTP call,
    returning raw reply text) and ``_extract_text(payload)`` is not
    needed: each subclass parses its own vendor payload.
    """

    name = "base"

    def __init__(self, model, max_tokens_per_run=DEFAULT_MAX_TOKENS_PER_RUN,
                 max_cost_usd_per_run=DEFAULT_MAX_COST_USD_PER_RUN,
                 max_attempts=DEFAULT_MAX_ATTEMPTS, backoff_seconds=0.0,
                 rates=None, transport=None):
        if max_tokens_per_run <= 0:
            raise LLMConfigError("max_tokens_per_run must be positive")
        if max_cost_usd_per_run < 0:
            raise LLMConfigError("max_cost_usd_per_run must not be negative")
        if max_attempts < 1:
            raise LLMConfigError("max_attempts must be at least 1")
        self.model = model
        self.max_tokens_per_run = max_tokens_per_run
        self.max_cost_usd_per_run = max_cost_usd_per_run
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.rates = rates if rates is not None else DEFAULT_RATES.get(
            self.name, (0.0, 0.0))
        self._transport = transport or _default_transport
        self._secrets = ()

    def estimate_cost(self, input_tokens, output_tokens):
        """Estimated USD cost from configured per-1k-token rates."""
        rate_in, rate_out = self.rates
        return (input_tokens / 1000.0) * rate_in \
            + (output_tokens / 1000.0) * rate_out

    def _check_caps(self, tokens_used, cost_estimate):
        if tokens_used > self.max_tokens_per_run:
            raise LLMCapError(
                f"token cap exceeded: {tokens_used} estimated tokens "
                f"> {self.max_tokens_per_run} allowed per run")
        if cost_estimate > self.max_cost_usd_per_run:
            raise LLMCapError(
                f"cost cap exceeded: estimated ${cost_estimate:.4f} "
                f"> ${self.max_cost_usd_per_run:.2f} allowed per run")

    def complete(self, prompt, schema=None, system=None):
        """Run one prompt to a (schema-validated) result.

        ``prompt`` is the user text; ``system`` is an optional system
        instruction. When ``schema`` is given, the reply must parse as
        JSON matching it, or a repair attempt is made, up to
        ``max_attempts`` total attempts. Raises ``LLMSchemaError`` if
        no attempt validates, ``LLMCapError`` if a cap would be
        exceeded, ``LLMProviderError`` if the provider keeps failing.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise LLMConfigError("prompt must be a non-empty string")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        tokens_used = 0
        cost_estimate = 0.0
        last_errors = []
        for attempt in range(1, self.max_attempts + 1):
            attempt_prompt = self._attempt_text(messages, last_errors)
            input_tokens = estimate_tokens(attempt_prompt)
            self._check_caps(tokens_used + input_tokens,
                             cost_estimate)
            try:
                raw = self._complete_once(messages, last_errors)
            except LLMProviderError as exc:
                logger.warning("provider %s attempt %d/%d failed: %s",
                               self.name, attempt, self.max_attempts,
                               redact_text(str(exc), self._secrets))
                if attempt == self.max_attempts or not exc.retryable:
                    # Re-raise redacted: a transport error can echo a
                    # URL or header, so the original message must never
                    # escape this module unredacted.
                    raise LLMProviderError(
                        redact_text(str(exc), self._secrets),
                        retryable=exc.retryable) from exc
                self._sleep(attempt)
                continue
            output_tokens = estimate_tokens(raw)
            tokens_used += input_tokens + output_tokens
            # Cost is estimated on the running token total, split
            # evenly between input and output (both are estimates).
            cost_estimate = self.estimate_cost(tokens_used // 2,
                                               tokens_used // 2)
            self._check_caps(tokens_used, cost_estimate)
            if schema is None:
                return LLMResult(raw, None, tokens_used, cost_estimate,
                                 self.name, self.model, attempt)
            parsed, errors = self._parse_and_validate(raw, schema)
            if not errors:
                return LLMResult(raw, parsed, tokens_used, cost_estimate,
                                 self.name, self.model, attempt)
            last_errors = errors
            logger.warning("provider %s attempt %d/%d failed schema: %s",
                           self.name, attempt, self.max_attempts,
                           "; ".join(errors))
            if attempt == self.max_attempts:
                raise LLMSchemaError(
                    "reply did not match schema after "
                    f"{self.max_attempts} attempts: " + "; ".join(errors))
            self._sleep(attempt)
        raise LLMSchemaError("no attempts made")  # unreachable

    def _attempt_text(self, messages, last_errors):
        text = "\n".join(m["content"] for m in messages)
        if last_errors:
            text += ("\n\nYour previous reply failed validation:\n- "
                     + "\n- ".join(last_errors)
                     + "\nReply with valid JSON only, matching the schema.")
        return text

    def _parse_and_validate(self, raw, schema):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, [f"reply is not valid JSON: {exc.msg}"]
        return parsed, validate_json(parsed, schema)

    def _sleep(self, attempt):
        if self.backoff_seconds > 0:
            time.sleep(self.backoff_seconds * attempt)

    def _complete_once(self, messages, last_errors):
        raise NotImplementedError

    def __repr__(self):
        return (f"{type(self).__name__}(model={self.model!r}, "
                f"max_tokens_per_run={self.max_tokens_per_run}, "
                f"max_cost_usd_per_run={self.max_cost_usd_per_run})")


class FakeProvider(BaseProvider):
    """Deterministic offline provider for tests and CI.

    ``script`` is a list played back in order: each entry is either a
    string (the reply text for that call) or an Exception to raise
    (a transport failure, consuming one attempt). When the script runs
    out, the last string entry repeats. Every call charges exactly
    ``tokens_per_call`` (a ``(input, output)`` pair), so cap tests are
    exact. No network, no key, no randomness.
    """

    name = "fake"

    def __init__(self, script=None, tokens_per_call=(10, 20), **kwargs):
        super().__init__(model=kwargs.pop("model", "fake"), **kwargs)
        self._script = list(script) if script else ['{"ok": true}']
        self._tokens_per_call = tokens_per_call
        self.calls = 0

    def _complete_once(self, messages, last_errors):
        self.calls += 1
        index = min(self.calls - 1, len(self._script) - 1)
        entry = self._script[index]
        if isinstance(entry, Exception):
            raise entry
        return entry

    def complete(self, prompt, schema=None, system=None):
        # The fake provider charges an exact fixed token count per
        # call, not a length estimate, so it runs its own attempt loop
        # with fixed charges instead of the shared estimated loop.
        return self._complete_with_fixed_tokens(prompt, schema, system)

    def _complete_with_fixed_tokens(self, prompt, schema=None, system=None):
        if not isinstance(prompt, str) or not prompt.strip():
            raise LLMConfigError("prompt must be a non-empty string")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        tokens_used = 0
        cost_estimate = 0.0
        last_errors = []
        for attempt in range(1, self.max_attempts + 1):
            input_tokens, output_tokens = self._tokens_per_call
            self._check_caps(tokens_used + input_tokens, cost_estimate)
            try:
                raw = self._complete_once(messages, last_errors)
            except LLMProviderError as exc:
                logger.warning("provider %s attempt %d/%d failed: %s",
                               self.name, attempt, self.max_attempts,
                               redact_text(str(exc), self._secrets))
                if attempt == self.max_attempts or not exc.retryable:
                    # Re-raise redacted: same rule as the shared loop.
                    raise LLMProviderError(
                        redact_text(str(exc), self._secrets),
                        retryable=exc.retryable) from exc
                self._sleep(attempt)
                continue
            except Exception as exc:
                # of any type is a transport failure for retry purposes.
                logger.warning("provider %s attempt %d/%d failed: %s",
                               self.name, attempt, self.max_attempts,
                               redact_text(str(exc), self._secrets))
                if attempt == self.max_attempts:
                    raise LLMProviderError(
                        f"fake provider failed: "
                        f"{redact_text(str(exc), self._secrets)}") from exc
                self._sleep(attempt)
                continue
            tokens_used += input_tokens + output_tokens
            cost_estimate = self.estimate_cost(tokens_used // 2,
                                               tokens_used // 2)
            self._check_caps(tokens_used, cost_estimate)
            if schema is None:
                return LLMResult(raw, None, tokens_used, cost_estimate,
                                 self.name, self.model, attempt)
            parsed, errors = self._parse_and_validate(raw, schema)
            if not errors:
                return LLMResult(raw, parsed, tokens_used, cost_estimate,
                                 self.name, self.model, attempt)
            last_errors = errors
            logger.warning("provider %s attempt %d/%d failed schema: %s",
                           self.name, attempt, self.max_attempts,
                           "; ".join(errors))
            if attempt == self.max_attempts:
                raise LLMSchemaError(
                    "reply did not match schema after "
                    f"{self.max_attempts} attempts: " + "; ".join(errors))
            self._sleep(attempt)
        raise LLMSchemaError("no attempts made")  # unreachable


class _HttpProvider(BaseProvider):
    """Shared HTTP plumbing: JSON body in, JSON body out, status checks."""

    def _post_json(self, url, headers, payload):
        body = json.dumps(payload).encode("utf-8")
        status, raw = self._transport("POST", url, headers, body)
        if status in (429, 500, 502, 503, 504):
            raise LLMProviderError(
                f"provider returned retryable status {status}")
        if status < 200 or status >= 300:
            raise LLMProviderError(
                f"provider returned status {status}: "
                f"{redact_text(raw[:200].decode('utf-8', 'replace'), self._secrets)}",
                retryable=False)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LLMProviderError(
                f"provider returned unparseable JSON: {exc}") from exc

    def _messages_for_api(self, messages, last_errors):
        """Repair note goes on the last user message; most chat APIs
        take a plain message list, so rebuild it here."""
        rebuilt = [dict(m) for m in messages]
        if last_errors:
            rebuilt[-1] = dict(rebuilt[-1])
            rebuilt[-1]["content"] += (
                "\n\nYour previous reply failed validation:\n- "
                + "\n- ".join(last_errors)
                + "\nReply with valid JSON only, matching the schema.")
        return rebuilt


class OllamaProvider(_HttpProvider):
    """Local Ollama (free, no key). Host from ``OLLAMA_HOST`` or arg."""

    name = "ollama"

    def __init__(self, host=None, model="llama3.1", **kwargs):
        super().__init__(model=model, **kwargs)
        self.host = (host or os.environ.get("OLLAMA_HOST")
                     or "http://localhost:11434").rstrip("/")

    def _complete_once(self, messages, last_errors):
        payload = {
            "model": self.model,
            "messages": self._messages_for_api(messages, last_errors),
            "stream": False,
        }
        data = self._post_json(f"{self.host}/api/chat",
                               {"Content-Type": "application/json"}, payload)
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMProviderError(
                "ollama reply missing message.content") from exc


class _KeyedProvider(_HttpProvider):
    """A provider that authenticates with a key from the environment.

    The key is kept in ``self._api_key`` (used only to build the
    Authorization/header value) and in ``self._secrets`` so every log
    line and exception passes through redaction. ``__repr__`` never
    shows it.
    """

    def __init__(self, model, env_var, api_key=None, **kwargs):
        super().__init__(model=model, **kwargs)
        key = api_key if api_key is not None else os.environ.get(env_var)
        if not key:
            raise LLMConfigError(
                f"no API key: set the {env_var} environment variable "
                f"or pass api_key explicitly (the key itself is never "
                f"logged)")
        self._api_key = key
        self._secrets = (key,)
        self._env_var = env_var

    def __repr__(self):
        return (f"{type(self).__name__}(model={self.model!r}, "
                f"key_from={self._env_var!r}, key_set=True, "
                f"max_tokens_per_run={self.max_tokens_per_run}, "
                f"max_cost_usd_per_run={self.max_cost_usd_per_run})")


class OpenAICompatibleProvider(_KeyedProvider):
    """Any OpenAI-compatible ``/chat/completions`` endpoint."""

    name = "openai-compatible"

    def __init__(self, api_key=None, base_url=None, model="gpt-4o-mini",
                 env_var="OPENAI_API_KEY", **kwargs):
        super().__init__(model=model, env_var=env_var, api_key=api_key,
                         **kwargs)
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")

    def _complete_once(self, messages, last_errors):
        payload = {
            "model": self.model,
            "messages": self._messages_for_api(messages, last_errors),
        }
        data = self._post_json(
            f"{self.base_url}/chat/completions",
            {"Content-Type": "application/json",
             "Authorization": f"Bearer {self._api_key}"},
            payload)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                "openai-compatible reply missing choices[0].message.content"
            ) from exc


class AnthropicProvider(_KeyedProvider):
    """Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, api_key=None, model="claude-sonnet-4-5",
                 env_var="ANTHROPIC_API_KEY", max_tokens=1024, **kwargs):
        super().__init__(model=model, env_var=env_var, api_key=api_key,
                         **kwargs)
        self.max_tokens = max_tokens

    def _complete_once(self, messages, last_errors):
        api_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in self._messages_for_api(messages, last_errors)
            if m["role"] != "system"
        ]
        system = next((m["content"] for m in messages
                       if m["role"] == "system"), None)
        payload = {"model": self.model, "max_tokens": self.max_tokens,
                   "messages": api_messages}
        if system:
            payload["system"] = system
        data = self._post_json(
            "https://api.anthropic.com/v1/messages",
            {"Content-Type": "application/json",
             "x-api-key": self._api_key,
             "anthropic-version": "2023-06-01"},
            payload)
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                "anthropic reply missing content[0].text") from exc


class GeminiProvider(_KeyedProvider):
    """Google Gemini API (key as a header, never in the URL)."""

    name = "gemini"

    def __init__(self, api_key=None, model="gemini-2.5-flash",
                 env_var="GEMINI_API_KEY", **kwargs):
        super().__init__(model=model, env_var=env_var, api_key=api_key,
                         **kwargs)

    def _complete_once(self, messages, last_errors):
        text = "\n".join(
            m["content"]
            for m in self._messages_for_api(messages, last_errors))
        payload = {"contents": [{"parts": [{"text": text}]}]}
        data = self._post_json(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent",
            {"Content-Type": "application/json",
             "x-goog-api-key": self._api_key},
            payload)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                "gemini reply missing candidates[0].content.parts[0].text"
            ) from exc


_PROVIDERS = {
    "fake": FakeProvider,
    "ollama": OllamaProvider,
    "openai-compatible": OpenAICompatibleProvider,
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


def get_provider(name, **kwargs):
    """Build a provider by name: ``fake``, ``ollama``,
    ``openai-compatible`` (alias ``openai``), ``anthropic``, ``gemini``.

    CI and tests must use ``fake`` — no real keys anywhere, no network.
    """
    try:
        cls = _PROVIDERS[name]
    except KeyError as exc:
        raise LLMConfigError(
            f"unknown provider {name!r}; known providers: "
            f"{', '.join(sorted(_PROVIDERS))}") from exc
    return cls(**kwargs)


def provider_from_env(**kwargs):
    """Pick a provider from the environment, cheapest first.

    A set key wins over local Ollama, in this order: OpenAI-compatible,
    Anthropic, Gemini. With no key set, local Ollama is the default —
    free, and the only provider that needs no credential at all.
    """
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAICompatibleProvider(**kwargs)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider(**kwargs)
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiProvider(**kwargs)
    return OllamaProvider(**kwargs)


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_COST_USD_PER_RUN",
    "DEFAULT_MAX_TOKENS_PER_RUN",
    "AnthropicProvider",
    "BaseProvider",
    "FakeProvider",
    "GeminiProvider",
    "LLMCapError",
    "LLMConfigError",
    "LLMError",
    "LLMProviderError",
    "LLMResult",
    "LLMSchemaError",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "estimate_tokens",
    "get_provider",
    "provider_from_env",
    "redact_text",
    "validate_json",
]
