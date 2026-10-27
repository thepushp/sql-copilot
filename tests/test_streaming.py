from types import SimpleNamespace

from langchain_core.messages import AIMessageChunk

from services.query_service import _call


class Streamer:
    def __init__(self, pieces, fail_after=None):
        self.pieces = pieces
        self.fail_after = fail_after
        self.invoked = 0

    def stream(self, messages):
        for i, piece in enumerate(self.pieces):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("boom")
            yield AIMessageChunk(
                content=piece,
                usage_metadata=(
                    {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7} if i == 0 else None
                ),
            )

    def invoke(self, messages):
        self.invoked += 1
        return SimpleNamespace(content="SELECT 1", usage_metadata={})


def test_streams_partial_text_and_merges_chunks():
    seen = []
    llm = Streamer(["SELECT ", "name ", "FROM t"])

    response = _call(llm, "x", seen.append)

    assert seen == ["SELECT ", "SELECT name ", "SELECT name FROM t"]
    assert response.content == "SELECT name FROM t"
    assert response.usage_metadata["input_tokens"] == 7
    assert llm.invoked == 0


def test_without_callback_uses_invoke():
    llm = Streamer(["a"])

    assert _call(llm, "x").content == "SELECT 1"
    assert llm.invoked == 1


def test_model_without_stream_uses_invoke():
    llm = SimpleNamespace(invoke=lambda m: SimpleNamespace(content="SELECT 2", usage_metadata={}))

    assert _call(llm, "x", lambda t: None).content == "SELECT 2"


def test_failure_before_first_chunk_falls_back_to_invoke():
    llm = Streamer(["a"], fail_after=0)

    assert _call(llm, "x", lambda t: None).content == "SELECT 1"


def test_failure_midway_is_raised():
    llm = Streamer(["a", "b"], fail_after=1)

    try:
        _call(llm, "x", lambda t: None)
    except RuntimeError:
        return
    raise AssertionError("expected the error")
