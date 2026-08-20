from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"LLM HTTP {error.code}: {detail}") from error


def chat(provider: dict[str, Any], messages: list[dict[str, str]], schema: dict[str, Any] | None = None) -> str:
    kind = provider.get("kind", "openai")
    base_url = str(provider.get("baseUrl") or "").rstrip("/")
    model = str(provider.get("model") or "")
    api_key = str(provider.get("apiKey") or "")
    timeout = int(provider.get("timeout", 180))
    if not base_url or not model:
        raise ValueError("LLM provider 缺少 baseUrl 或 model")
    if kind == "ollama":
        data = _post_json(
            f"{base_url}/api/chat",
            {"model": model, "messages": messages, "stream": False, "format": schema or "json"},
            {}, timeout,
        )
        return str(data.get("message", {}).get("content", ""))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.1}
    if schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "anima_caption", "schema": schema}}
    data = _post_json(f"{base_url}/chat/completions", payload, headers, timeout)
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("OpenAI-compatible endpoint 返回了无法识别的响应") from error


CAPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "quality_meta_year_safety", "subject_count", "characters", "series", "artists", "general",
        "natural_language", "warnings",
    ],
    "properties": {
        "quality_meta_year_safety": {"type": "array", "items": {"type": "string"}},
        "subject_count": {"type": "array", "items": {"type": "string"}},
        "characters": {"type": "array", "items": {"type": "string"}},
        "series": {"type": "array", "items": {"type": "string"}},
        "artists": {"type": "array", "items": {"type": "string"}},
        "general": {"type": "array", "items": {"type": "string"}},
        "natural_language": {"type": ["string", "null"]},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def mock_refine(profile: str, trigger: str, wd: dict[str, Any], joy: str) -> dict[str, Any]:
    general = [item["tag"] for item in wd.get("general", []) if isinstance(item, dict) and item.get("tag")]
    characters = [item["tag"] for item in wd.get("character", []) if isinstance(item, dict) and item.get("tag")]
    subject_count = [tag for tag in general if str(tag).replace("_", "") in {"1girl", "1boy", "1other"}]
    general = [tag for tag in general if tag not in subject_count]
    return {
        "quality_meta_year_safety": [],
        "subject_count": subject_count,
        "characters": characters if profile != "style" else [],
        "series": [],
        "artists": [],
        "general": general,
        "natural_language": joy if profile == "style" else None,
        "warnings": [],
    }
