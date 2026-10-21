MODEL_CONFIG = {
    "Ollama": {
        "models": [
            "gemma4:latest",
            "llama3.2",
            "gemma3",
            "qwen3",
            "mistral",
        ],
        "profile": "Compact",
        "needs_key": False,
        "needs_base_url": True,
        "default_base_url": "http://localhost:11434",
        "key_env": None,
    },

    "OpenAI": {
        "models": [
            "gpt-4o-mini",
            "gpt-4o",
        ],
        "profile": "Standard",
        "needs_key": True,
        "needs_base_url": False,
        "default_base_url": "",
        "key_env": "OPENAI_API_KEY",
    },

    "Anthropic": {
        "models": [
            "claude-sonnet-5-5",
            "claude-opus-5-5",
            "claude-haiku-5-5",
        ],
        "profile": "Large",
        "needs_key": True,
        "needs_base_url": False,
        "default_base_url": "",
        "key_env": "ANTHROPIC_API_KEY",
    },

    "Google": {
        "models": [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
        ],
        "profile": "Large",
        "needs_key": True,
        "needs_base_url": False,
        "default_base_url": "",
        "key_env": "GOOGLE_API_KEY",
    },

    "OpenAI-compatible": {
        "models": [],
        "profile": "Standard",
        "needs_key": True,
        "needs_base_url": True,
        "default_base_url": "",
        "key_env": "OPENAI_COMPATIBLE_API_KEY",
    },
}


CONTEXT_PROFILES = {
    "Compact": {
        "k": 5,
        "shortlist": 18,
        "fetch_k": 25,
        "max_neighbors": 3,
        "keyword_hits": 3,
        "max_chars": 6000,
        "include_indexes": False,
        "history_turns": 2,
        "examples": 2,
    },
    "Standard": {
        "k": 8,
        "shortlist": 28,
        "fetch_k": 40,
        "max_neighbors": 6,
        "keyword_hits": 4,
        "max_chars": 16000,
        "include_indexes": False,
        "history_turns": 3,
        "examples": 3,
    },
    "Large": {
        "k": 12,
        "shortlist": 40,
        "fetch_k": 60,
        "max_neighbors": 10,
        "keyword_hits": 6,
        "max_chars": 48000,
        "include_indexes": True,
        "history_turns": 5,
        "examples": 4,
    },
}
