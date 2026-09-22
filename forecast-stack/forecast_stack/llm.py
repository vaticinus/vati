"""Provider-agnostic LLM client — one interface, any endpoint, stdlib only.

Works with any OpenAI-compatible API (OpenRouter, Fireworks, DeepSeek, OpenAI,
Together, Groq, vLLM, Ollama, LM Studio, ...) plus Anthropic's Messages API.
No SDK, no lock-in: set one API key and go.

    from forecast_stack.llm import complete, complete_json

    complete("Name the capital of France.")
    complete_json("Return {\"p\": 0.7} for a coin flip.", provider="deepseek")

Keys (any one is enough; the first found wins):
    OPENROUTER_API_KEY  FIREWORKS_API_KEY  DEEPSEEK_API_KEY  OPENAI_API_KEY
    TOGETHER_API_KEY    GROQ_API_KEY       ANTHROPIC_API_KEY

Overrides:
    FORECAST_STACK_PROVIDER   force a provider by name
    FORECAST_STACK_MODEL      force a model id
    FORECAST_STACK_BASE_URL   custom OpenAI-compatible endpoint
    FORECAST_STACK_MOCK=1     deterministic offline provider (tests, demos)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class LLMError(RuntimeError):
    """A provider, transport, or parsing failure."""


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    default_model: str
    kind: str = "openai"  # openai | anthropic | mock


PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider("openrouter", "https://openrouter.ai/api/v1",
                           "OPENROUTER_API_KEY", "deepseek/deepseek-chat"),
    "fireworks": Provider("fireworks", "https://api.fireworks.ai/inference/v1",
                          "FIREWORKS_API_KEY", "accounts/fireworks/models/deepseek-v3"),
    "deepseek": Provider("deepseek", "https://api.deepseek.com/v1",
                         "DEEPSEEK_API_KEY", "deepseek-chat"),
    "openai": Provider("openai", "https://api.openai.com/v1",
                       "OPENAI_API_KEY", "gpt-4o-mini"),
    "together": Provider("together", "https://api.together.xyz/v1",
                         "TOGETHER_API_KEY", "deepseek-ai/DeepSeek-V3"),
    "groq": Provider("groq", "https://api.groq.com/openai/v1",
                     "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "anthropic": Provider("anthropic", "https://api.anthropic.com/v1",
                          "ANTHROPIC_API_KEY", "claude-3-5-haiku-latest", kind="anthropic"),
    "mock": Provider("mock", "", "", "mock-1", kind="mock"),
}

_ENV_LOADED = False


def load_dotenv(path: str | Path | None = None) -> None:
    """Populate os.environ from a .env file (no dependency). Never overwrites."""
    global _ENV_LOADED
    candidates = [Path(path)] if path else [Path.cwd() / ".env",
                                            Path(__file__).resolve().parents[1] / ".env"]
    for candidate in candidates:
        if not candidate or not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
    _ENV_LOADED = True


def available_providers() -> list[str]:
    """Providers with a key present in the environment (mock always available)."""
    if not _ENV_LOADED:
        load_dotenv()
    if os.environ.get("FORECAST_STACK_MOCK") == "1":
        return ["mock"]
    out = []
    for name, provider in PROVIDERS.items():
        if name == "mock":
            continue
        if provider.key_env and os.environ.get(provider.key_env):
            out.append(name)
    if os.environ.get("FORECAST_STACK_BASE_URL"):
        out.insert(0, "custom")
    return out


def default_provider() -> str:
    forced = os.environ.get("FORECAST_STACK_PROVIDER")
    if forced:
        if forced not in PROVIDERS and forced != "custom":
            raise LLMError(f"unknown provider {forced!r}; known: {sorted(PROVIDERS)}")
        return forced
    found = available_providers()
    if not found:
        raise LLMError(
            "no LLM provider configured. Set one of: "
            + ", ".join(sorted(p.key_env for p in PROVIDERS.values() if p.key_env))
            + "  (or FORECAST_STACK_MOCK=1 for the offline provider)"
        )
    return found[0]


def _provider(name: str) -> Provider:
    if name == "custom":
        return Provider("custom", os.environ["FORECAST_STACK_BASE_URL"].rstrip("/"),
                        "FORECAST_STACK_API_KEY", os.environ.get("FORECAST_STACK_MODEL", "default"))
    if name not in PROVIDERS:
        raise LLMError(f"unknown provider {name!r}; known: {sorted(PROVIDERS)}")
    return PROVIDERS[name]


def _key(provider: Provider) -> str:
    if provider.kind == "mock":
        return ""
    key = os.environ.get(provider.key_env, "")
    if not key and provider.name == "custom":
        key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise LLMError(f"{provider.name}: {provider.key_env} is not set")
    return key


def _post(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        raise LLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"connection failed for {url}: {exc.reason}") from exc


# ── the offline provider ─────────────────────────────────────────────────────
_MOCK_CALLS = 0


def _mock_complete(prompt: str, system: str | None, model: str) -> str:
    """Keyless stand-in: shaped like a real answer, stable within a process.

    Successive calls differ (a call counter enters the hash) so self-consistency
    sampling produces a genuine spread. It is deliberately not a forecaster — it
    exists so tests, CI, and the demo run end to end with no keys and no network.
    """
    global _MOCK_CALLS
    _MOCK_CALLS += 1
    digest = hashlib.sha256(f"{_MOCK_CALLS}:{system or ''}:{prompt}".encode()).hexdigest()
    value = int(digest[:8], 16) / 0xFFFFFFFF
    probability = round(min(0.95, max(0.05, 0.15 + 0.7 * value)), 2)
    if re.search(r"probability", prompt, re.I):
        return f"Reference class and forces considered.\nProbability: {probability}"
    return f"[mock:{model}] {digest[:16]}"


# ── the one entry point ──────────────────────────────────────────────────────
def complete(
    prompt: str,
    *,
    system: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    timeout: float = 120.0,
    retries: int = 2,
    response_format: str | None = None,
) -> str:
    """Return the model's text completion for ``prompt``."""
    name = provider or default_provider()
    spec = _provider(name)
    model_id = model or os.environ.get("FORECAST_STACK_MODEL") or spec.default_model

    if spec.kind == "mock":
        return _mock_complete(prompt, system, model_id)

    if spec.kind == "anthropic":
        payload: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        headers = {"x-api-key": _key(spec), "anthropic-version": "2023-06-01"}
        url = f"{spec.base_url}/messages"
    else:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        payload = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {_key(spec)}"}
        if spec.name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/forecast-stack"
            headers["X-Title"] = "forecast-stack"
        url = f"{spec.base_url}/chat/completions"

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            data = _post(url, payload, headers, timeout)
            if spec.kind == "anthropic":
                blocks = data.get("content") or []
                return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            choices = data.get("choices") or []
            if not choices:
                raise LLMError(f"empty response: {json.dumps(data)[:300]}")
            return choices[0].get("message", {}).get("content") or ""
        except LLMError as exc:
            last = exc
            message = str(exc)
            if "HTTP 4" in message and "429" not in message:
                break  # bad key / bad model id — retrying will not help
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last if last else LLMError("completion failed")


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response."""
    if not text:
        raise LLMError("empty response, no JSON to extract")
    fence = _JSON_FENCE.search(text)
    candidates = [fence.group(1)] if fence else []
    candidates.append(text)
    depth, start = 0, None
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:index + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMError(f"no JSON object found in response: {text[:200]!r}")


def complete_json(
    prompt: str,
    *,
    system: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    **kwargs: Any,
) -> dict:
    """Completion parsed as a JSON object (fenced or bare)."""
    text = complete(prompt, system=system, provider=provider, model=model,
                    max_tokens=max_tokens, temperature=temperature,
                    response_format="json", **kwargs)
    return extract_json(text)
