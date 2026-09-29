"""Anrak Jolly provider plugin: task polling, route replay, streaming, auth."""

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

from providers import get_provider_profile

JOLLY_URL = "https://anrak.legal/api/jolly/chat/completions"


@pytest.fixture
def jolly():
    profile = get_provider_profile("anrak")
    assert profile is not None
    mod = sys.modules[type(profile).__module__]
    mod._ROUTES.clear()
    return profile, mod


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch, jolly):
    _, mod = jolly
    monkeypatch.setattr(mod, "_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)


def _completion(content=None, tool_calls=None, route=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    out = {
        "id": "c1",
        "object": "chat.completion",
        "created": 1,
        "model": "anraklegal/jolly",
        "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
    }
    if route is not None:
        out["jolly"] = {"route": route}
    return out


def _client(profile, handler):
    return httpx.Client(transport=profile.wrap_http_transport(httpx.MockTransport(handler)))


def _post(client, body):
    return client.post(JOLLY_URL, json=body, headers={"Authorization": "Bearer k"})


def test_profile_is_registered_as_api_key_provider():
    from hermes_cli.auth import PROVIDER_REGISTRY

    cfg = PROVIDER_REGISTRY["anrak"]
    assert cfg.auth_type == "api_key"
    assert cfg.inference_base_url.rstrip("/").endswith("/api/jolly")
    assert "ANRAK_JOLLY_API_KEY" in cfg.api_key_env_vars


def test_plain_completion_passes_through(jolly, monkeypatch):
    profile, _ = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion("Hi"))

    resp = _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.json()["choices"][0]["message"]["content"] == "Hi"
    assert "task" not in seen[0]


def test_drafting_task_is_polled_to_completion(jolly, monkeypatch):
    profile, _ = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")
    polls = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"task": {"id": "t1", "status": "queued", "poll_url": "/api/jolly/tasks/t1"}})
        polls.append(str(request.url))
        assert request.headers["authorization"] == "Bearer k"
        if len(polls) < 2:
            return httpx.Response(200, json={"task": {"id": "t1", "status": "running"}})
        return httpx.Response(200, json={"task": {"id": "t1", "status": "completed"}, "result": _completion("Draft")})

    resp = _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": [{"role": "user", "content": "draft"}]})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Draft"
    assert polls[0] == "https://anrak.legal/api/jolly/tasks/t1"


def test_completed_task_with_bare_text_is_synthesized(jolly, monkeypatch):
    profile, _ = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"poll_url": "https://anrak.legal/api/jolly/tasks/t2", "status": "queued"})
        return httpx.Response(200, json={"status": "completed", "output": "NDA text"})

    resp = _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": []})
    assert resp.json()["choices"][0]["message"] == {"role": "assistant", "content": "NDA text"}


def test_failed_task_surfaces_as_api_error(jolly, monkeypatch):
    profile, _ = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"poll_url": "/t", "status": "queued"})
        return httpx.Response(200, json={"status": "failed", "error": {"message": "quota"}})

    resp = _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": []})
    assert resp.status_code >= 500
    assert resp.json()["error"]["message"] == "quota"


def test_route_is_replayed_as_task_on_tool_follow_up(jolly, monkeypatch):
    profile, _ = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")
    bodies = []
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}

    def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, json=_completion(tool_calls=[tool_call], route="route-abc"))
        return httpx.Response(200, json=_completion("done"))

    client = _client(profile, handler)
    history = [{"role": "user", "content": "read it"}]
    _post(client, {"model": "anraklegal/jolly", "messages": history})
    history += [
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "tool", "tool_call_id": "call_1", "content": "file body"},
    ]
    _post(client, {"model": "anraklegal/jolly", "messages": history})

    assert "task" not in bodies[0]
    assert bodies[1]["task"] == "route-abc"
    assert bodies[1]["messages"] == history  # complete history preserved

    # A fresh user turn after a plain answer is not a tool continuation.
    history += [{"role": "assistant", "content": "done"}, {"role": "user", "content": "next"}]
    _post(client, {"model": "anraklegal/jolly", "messages": history})
    assert "task" not in bodies[2]


def _sse_events(resp):
    events = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line[6:]))
    return events


def test_json_answer_to_stream_request_is_reemitted_as_sse(jolly, monkeypatch):
    profile, _ = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")
    tool_call = {"id": "call_9", "type": "function", "function": {"name": "web_search", "arguments": "{\"q\":1}"}}

    def handler(request):
        return httpx.Response(200, json=_completion("Hello", tool_calls=[tool_call], route="r9"))

    resp = _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": [], "stream": True})
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.text.rstrip().endswith("data: [DONE]")
    events = _sse_events(resp)
    delta = events[0]["choices"][0]["delta"]
    assert delta["content"] == "Hello"
    assert delta["tool_calls"][0]["id"] == "call_9"
    assert delta["tool_calls"][0]["function"]["arguments"] == "{\"q\":1}"
    assert events[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_stream_rejection_retries_without_stream(jolly, monkeypatch):
    profile, _ = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("stream"):
            return httpx.Response(400, json={"error": {"message": "stream is not supported"}})
        return httpx.Response(200, json=_completion("ok"))

    resp = _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": [], "stream": True})
    assert resp.status_code == 200
    assert "stream" not in bodies[1]
    assert _sse_events(resp)[0]["choices"][0]["delta"]["content"] == "ok"


def test_native_sse_passes_through_and_records_route(jolly, monkeypatch):
    profile, mod = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_s", "function": {"name": "x", "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "jolly": {"route": "sse-route"}},
    ]
    raw = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw)

    resp = _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": [], "stream": True})
    assert resp.read() == raw
    assert mod._route_for([{"role": "assistant", "tool_calls": [{"id": "call_s"}]}]) == "sse-route"


# ── Credentials: API key first, Anrak sign-in token as fallback ──────────


def _write_tokens(home: Path, **tokens):
    token_dir = home / "mcp-tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / "Anrak_Legal.json").write_text(json.dumps(tokens), encoding="utf-8")
    return token_dir


def test_sign_in_token_is_the_credential_fallback(monkeypatch):
    from hermes_cli.auth import resolve_api_key_provider_credentials

    monkeypatch.delenv("ANRAK_JOLLY_API_KEY", raising=False)
    home = Path(os.environ["HERMES_HOME"])
    assert resolve_api_key_provider_credentials("anrak")["api_key"] == ""

    _write_tokens(home, access_token="oauth-tok", expires_at=time.time() + 3600)
    creds = resolve_api_key_provider_credentials("anrak")
    assert creds["api_key"] == "oauth-tok"
    assert creds["source"] == "profile:anrak"

    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "real-key")
    assert resolve_api_key_provider_credentials("anrak")["api_key"] == "real-key"


def test_expired_sign_in_token_is_refreshed_before_request(jolly, monkeypatch):
    profile, mod = jolly
    monkeypatch.delenv("ANRAK_JOLLY_API_KEY", raising=False)
    home = Path(os.environ["HERMES_HOME"])
    token_dir = _write_tokens(home, access_token="old", refresh_token="rt", expires_at=time.time() - 10)
    (token_dir / "Anrak_Legal.meta.json").write_text(json.dumps({"token_endpoint": "https://anrak.legal/oauth/token"}))
    (token_dir / "Anrak_Legal.client.json").write_text(json.dumps({"client_id": "cid"}))

    refreshes = []

    def fake_post(url, data=None, auth=None, timeout=None):
        refreshes.append((url, dict(data)))
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "rt2", "expires_in": 3600})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    seen_auth = []

    def handler(request):
        seen_auth.append(request.headers["authorization"])
        return httpx.Response(200, json=_completion("ok"))

    _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": []})

    assert seen_auth == ["Bearer new"]
    assert refreshes[0][0] == "https://anrak.legal/oauth/token"
    assert refreshes[0][1]["grant_type"] == "refresh_token"
    stored = json.loads((token_dir / "Anrak_Legal.json").read_text())
    assert stored["access_token"] == "new" and stored["refresh_token"] == "rt2"
    assert stored["expires_at"] > time.time()


def test_api_key_is_sent_as_is_when_configured(jolly, monkeypatch):
    profile, _ = jolly
    monkeypatch.setenv("ANRAK_JOLLY_API_KEY", "k")
    _write_tokens(Path(os.environ["HERMES_HOME"]), access_token="oauth-tok")
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json=_completion("ok"))

    _post(_client(profile, handler), {"model": "anraklegal/jolly", "messages": []})
    assert seen == ["Bearer k"]
