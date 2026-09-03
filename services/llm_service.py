import os

from langchain_anthropic import (
    ChatAnthropic,
)

from langchain_google_genai import (
    ChatGoogleGenerativeAI,
)

from langchain_ollama import (
    ChatOllama,
)

from langchain_openai import (
    ChatOpenAI,
)


def get_llm(
    provider,
    model,
    temperature=0.1,
):

    if provider == "Ollama":

        return ChatOllama(
            model=model,
            temperature=temperature,
            base_url=os.getenv(
                "OLLAMA_BASE_URL",
                "http://localhost:11434",
            ),
        )

    if provider == "OpenAI":

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=os.getenv(
                "OPENAI_API_KEY"
            ),
        )

    if provider == "Anthropic":

        return ChatAnthropic(
            model=model,
            temperature=temperature,
            api_key=os.getenv(
                "ANTHROPIC_API_KEY"
            ),
        )

    if provider == "Google":

        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=os.getenv(
                "GOOGLE_API_KEY"
            ),
        )

    raise ValueError(
        f"Unsupported provider: {provider}"
    )