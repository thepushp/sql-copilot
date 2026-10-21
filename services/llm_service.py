import os

from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI


DEFAULT_TIMEOUT = 120


def get_llm(
    provider,
    model,
    temperature=0.1,
    api_key=None,
    base_url=None,
    timeout=DEFAULT_TIMEOUT,
):

    if provider == "Ollama":

        return ChatOllama(
            model=model,
            temperature=temperature,
            base_url=(
                base_url
                or os.getenv(
                    "OLLAMA_BASE_URL",
                    "http://localhost:11434",
                )
            ),
            client_kwargs={"timeout": timeout},
        )

    if provider == "OpenAI":

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            timeout=timeout,
        )

    if provider == "OpenAI-compatible":

        if not base_url:
            raise ValueError(
                "A base URL is required for an "
                "OpenAI-compatible provider."
            )

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            base_url=base_url,
            api_key=(
                api_key
                or os.getenv("OPENAI_COMPATIBLE_API_KEY")
                or "not-needed"
            ),
            timeout=timeout,
        )

    if provider == "Anthropic":

        return ChatAnthropic(
            model=model,
            temperature=temperature,
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
            timeout=timeout,
        )

    if provider == "Google":

        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=api_key or os.getenv("GOOGLE_API_KEY"),
            timeout=timeout,
        )

    raise ValueError(f"Unsupported provider: {provider}")
