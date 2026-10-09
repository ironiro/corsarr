"""Other language model providers (OpenAI, Gemini, Ollama, LM Studio) – against a simulated server."""
import asyncio
import json

import httpx
import pytest

from corsarr import config, llm
from corsarr.llm import LLMFailed, LLMUnavailable, Selection, Understanding


def backend(provider, handler, url=""):
    b = llm._OpenAICompatible(provider, "test-model", "key" if provider in ("openai", "gemini") else "", url)
    b.client = httpx.AsyncClient(base_url=b.client.base_url, headers=b.client.headers,
                                 transport=httpx.MockTransport(handler))
    return b


def answer(content, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


SELECTION = {"intro": "two picks", "picks": [{"id": 0, "reason": "fits"}]}


def test_structured_answer_with_schema_and_code_fence():
    seen = []

    def handler(request):
        seen.append((request.url, request.headers.get("authorization"), json.loads(request.content)))
        return answer("Here you go:\n```json\n" + json.dumps(SELECTION) + "\n```")

    b = backend("openai", handler)
    sel = asyncio.run(b.generate("system", "pick", Selection, 1000))
    assert isinstance(sel, Selection) and sel.picks[0].reason == "fits"
    url, auth, body = seen[0]
    assert str(url) == "https://api.openai.com/v1/chat/completions" and auth == "Bearer key"
    assert body["max_completion_tokens"] == 4000 and "max_tokens" not in body
    assert body["response_format"]["json_schema"]["name"] == "Selection"
    assert "JSON schema" in body["messages"][1]["content"]


def test_local_server_gets_v1_and_retries_without_schema_support():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if "response_format" in bodies[-1]:
            return httpx.Response(400, json={"error": {"message": "response_format not supported"}})
        return answer("<think>hmm</think>" + json.dumps(SELECTION))

    b = backend("ollama", handler, url="http://box:11434")
    assert str(b.client.base_url).rstrip("/") == "http://box:11434/v1"
    sel = asyncio.run(b.generate("system", "pick", Selection, 1000))
    assert sel.intro == "two picks" and len(bodies) == 2 and bodies[0]["max_tokens"] == 4000


def test_errors_map_to_outage_or_failed_answer():
    b = backend("gemini", lambda r: httpx.Response(401, json={"error": {"message": "API key not valid"}}))
    with pytest.raises(LLMUnavailable, match="API key not valid"):
        asyncio.run(b.generate("s", "u", None, 50))
    b = backend("lmstudio", lambda r: answer('{"intro": "cut off', finish="length"))
    with pytest.raises(LLMFailed):
        asyncio.run(b.generate("s", "u", Selection, 50))
    b = backend("lmstudio", lambda r: answer('{"intro": 3}'))
    with pytest.raises(LLMFailed, match="format"):
        asyncio.run(b.generate("s", "u", Selection, 50))

    def unreachable(request):
        raise httpx.ConnectError("Connection refused")
    with pytest.raises(LLMUnavailable):
        asyncio.run(backend("ollama", unreachable).generate("s", "u", None, 50))


def test_model_list_drops_non_chat_models_and_accepts_ollama_tags():
    ids = ["llama3.1:latest", "nomic-embed-text:latest", "qwen3:14b"]
    b = backend("ollama", lambda r: httpx.Response(200, json={"data": [{"id": i} for i in ids]}))
    assert asyncio.run(b.list_models()) == ["llama3.1:latest", "qwen3:14b"]
    b.model = "llama3.1"
    asyncio.run(b.check_model())
    b.model = "mistral"
    with pytest.raises(LLMUnavailable, match="not found"):
        asyncio.run(b.check_model())
    g = backend("gemini", lambda r: httpx.Response(200, json={"data": [
        {"id": "models/gemini-flash"}, {"id": "models/text-embedding-004"}, {"id": "models/gemma-3"}]}))
    assert asyncio.run(g.list_models()) == ["gemini-flash"]


def test_schema_is_flattened_for_simple_servers():
    schema = llm._inline_schema(Understanding.model_json_schema())
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and '"title"' not in text
    assert "series_pause_days" in schema["properties"]["settings"]["properties"]


def test_only_the_selected_providers_fields_are_required(tmp_path, monkeypatch):
    for f in config.FIELDS:
        monkeypatch.delenv(f.name, raising=False)
    monkeypatch.setenv("CORSARR_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    cfg = config.load()
    assert "ANTHROPIC_API_KEY" in cfg.errors and "OLLAMA_MODEL" not in cfg.errors
    assert cfg.llm_provider == "claude" and cfg.model == "claude-haiku-5-5"

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:14b")
    cfg = config.load()
    assert "ANTHROPIC_API_KEY" not in cfg.errors and "OLLAMA_MODEL" not in cfg.errors
    assert cfg.model == "qwen3:14b" and cfg.llm_url == "http://localhost:11434" and cfg.llm_api_key == ""
    model = llm.create(cfg)
    assert model.label == "Ollama" and model.provider == "ollama"
    asyncio.run(model.close())

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert {"OPENAI_API_KEY", "OPENAI_MODEL"} <= set(config.load().errors)
