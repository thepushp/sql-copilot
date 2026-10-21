import pytest

from services import model_catalog
from services.llm_service import get_llm
from services.model_catalog import list_models


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.fixture
def http(monkeypatch):
    calls = []

    def install(payload):
        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append({"url": url, "headers": headers or {}, "params": params or {}})
            return FakeResponse(payload)
        monkeypatch.setattr(model_catalog.httpx, "get", fake_get)
        return calls

    return install


class TestModelCatalog:

    def test_ollama(self, http):
        calls = http({"models": [{"name": "zeta:1"}, {"name": "alpha:latest"}]})

        assert list_models("Ollama", base_url="http://host:11434/") == ["alpha:latest", "zeta:1"]
        assert calls[0]["url"] == "http://host:11434/api/tags"

    def test_openai_keeps_chat_models_only(self, http):
        calls = http({"data": [
            {"id": "gpt-4o"}, {"id": "gpt-4o-audio-preview"}, {"id": "text-embedding-3-small"},
            {"id": "o3-mini"}, {"id": "dall-e-3"}, {"id": "whisper-1"}, {"id": "gpt-4o-mini"},
        ]})

        assert list_models("OpenAI", api_key="sk-test") == ["gpt-4o", "gpt-4o-mini", "o3-mini"]
        assert calls[0]["headers"]["Authorization"] == "Bearer sk-test"

    def test_openai_compatible_needs_base_url(self, http):
        http({"data": []})

        with pytest.raises(ValueError, match="base URL"):
            list_models("OpenAI-compatible")

    def test_openai_compatible(self, http):
        calls = http({"data": [{"id": "llama-3"}, {"id": "mixtral"}]})

        assert list_models("OpenAI-compatible", base_url="https://host/v1/") == ["llama-3", "mixtral"]
        assert calls[0]["url"] == "https://host/v1/models"

    def test_anthropic(self, http):
        calls = http({"data": [{"id": "claude-a"}, {"id": "claude-b"}]})

        assert list_models("Anthropic", api_key="k") == ["claude-a", "claude-b"]
        assert calls[0]["headers"]["x-api-key"] == "k"
        assert "anthropic-version" in calls[0]["headers"]

    def test_google_filters_by_generation_support(self, http):
        calls = http({"models": [
            {"name": "models/gemini-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
        ]})

        assert list_models("Google", api_key="k") == ["gemini-pro"]
        assert calls[0]["params"]["key"] == "k"

    def test_unsupported_provider(self):
        with pytest.raises(ValueError):
            list_models("Nope")


class TestLlmFactory:

    def test_ollama(self):
        llm = get_llm("Ollama", "gemma", 0.3, base_url="http://h:1")

        assert type(llm).__name__ == "ChatOllama"
        assert llm.model == "gemma"
        assert llm.base_url == "http://h:1"

    def test_openai(self):
        assert type(get_llm("OpenAI", "gpt-4o", api_key="sk-x")).__name__ == "ChatOpenAI"

    def test_openai_compatible_requires_base_url(self):
        with pytest.raises(ValueError, match="base URL"):
            get_llm("OpenAI-compatible", "m", api_key="k")

    def test_openai_compatible(self):
        llm = get_llm("OpenAI-compatible", "m", base_url="https://h/v1")

        assert type(llm).__name__ == "ChatOpenAI"
        assert "h/v1" in str(llm.openai_api_base)

    def test_anthropic_and_google(self):
        assert type(get_llm("Anthropic", "claude-x", api_key="k")).__name__ == "ChatAnthropic"
        assert type(get_llm("Google", "gemini-x", api_key="k")).__name__ == "ChatGoogleGenerativeAI"

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="Unsupported"):
            get_llm("Nope", "m")
