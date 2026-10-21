import os

import httpx


REQUEST_TIMEOUT = 8

NON_CHAT_MARKERS = (
    "audio",
    "realtime",
    "tts",
    "transcribe",
    "image",
    "embedding",
    "moderation",
    "search",
    "whisper",
    "dall-e",
)


def _get_json(url, headers=None, params=None):

    response = httpx.get(
        url,
        headers=headers,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def _openai_style(base_url, api_key):

    payload = _get_json(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key or 'x'}"},
    )

    return [item["id"] for item in payload.get("data", [])]


def list_models(provider, api_key=None, base_url=None):
    """Return model names available for the provider; raises on failure."""

    if provider == "Ollama":

        base = (
            base_url
            or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        )

        payload = _get_json(f"{base.rstrip('/')}/api/tags")

        return sorted(item["name"] for item in payload.get("models", []))

    if provider == "OpenAI":

        ids = _openai_style(
            "https://api.openai.com/v1",
            api_key or os.getenv("OPENAI_API_KEY"),
        )

        return sorted(
            model_id
            for model_id in ids
            if model_id.startswith(("gpt-", "chatgpt", "o1", "o3", "o4"))
            and not any(marker in model_id for marker in NON_CHAT_MARKERS)
        )

    if provider == "OpenAI-compatible":

        if not base_url:
            raise ValueError("Enter a base URL first.")

        return sorted(
            _openai_style(
                base_url,
                api_key or os.getenv("OPENAI_COMPATIBLE_API_KEY"),
            )
        )

    if provider == "Anthropic":

        payload = _get_json(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key or os.getenv("ANTHROPIC_API_KEY", ""),
                "anthropic-version": "2023-06-01",
            },
            params={"limit": 100},
        )

        return [item["id"] for item in payload.get("data", [])]

    if provider == "Google":

        payload = _get_json(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={
                "key": api_key or os.getenv("GOOGLE_API_KEY", ""),
                "pageSize": 100,
            },
        )

        return sorted(
            item["name"].removeprefix("models/")
            for item in payload.get("models", [])
            if "generateContent" in item.get("supportedGenerationMethods", [])
        )

    raise ValueError(f"Unsupported provider: {provider}")
