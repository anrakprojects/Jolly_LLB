"""Anrak Jolly provider profile (``anraklegal/jolly``).

Jolly is AnrakLegal's multimodal model, served OpenAI-style at
``POST https://anrak.legal/api/jolly/chat/completions``. It deviates from plain
chat-completions in three ways, all handled below the OpenAI SDK by
``JollyTransport`` (installed via ``ProviderProfile.wrap_http_transport``):

1. **Drafting tasks.** Long drafting requests return a task with a
   ``poll_url`` instead of a completion. The transport polls it until the task
   finishes and hands the SDK the finished completion.
2. **Tool-conversation routing.** A response carrying tool calls also carries
   ``jolly.route``. The follow-up request (with the tool results) must send
   that value as ``task`` alongside the complete message history. The
   transport remembers the route per tool-call id and re-attaches it.
3. **Streaming.** ``stream`` is passed through. A JSON (non-SSE) answer to a
   streaming request is re-emitted as SSE so the streaming consumer still
   works; a server that rejects ``stream`` is retried without it.

Credentials, in order:
  * ``ANRAK_JOLLY_API_KEY`` — a key issued at https://developers.anrak.legal/console
  * the user's Anrak Legal sign-in (the OAuth token the desktop app already
    obtains for the "Anrak Legal" MCP connector), refreshed on demand.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin

import httpx

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

JOLLY_MODEL = "anraklegal/jolly"
JOLLY_BASE_URL = "https://anrak.legal/api/jolly"
API_KEY_ENV = "ANRAK_JOLLY_API_KEY"
# MCP connector whose OAuth sign-in doubles as managed Jolly access.
ANRAK_MCP_SERVER = "Anrak Legal"

# Drafting tasks can run for minutes; poll with a bounded backoff.
_POLL_INTERVAL_S = 2.0
_POLL_INTERVAL_MAX_S = 10.0
_POLL_TIMEOUT_S = float(os.environ.get("ANRAK_JOLLY_POLL_TIMEOUT", "1200") or 1200)
_TASK_DONE = {"completed", "complete", "succeeded", "success", "done", "finished"}
_TASK_FAILED = {"failed", "error", "errored", "cancelled", "canceled", "expired", "rejected"}


# ── Anrak Legal OAuth token (shared with the MCP connector) ───────────────


def _token_paths() -> tuple[Path, Path, Path]:
    from hermes_constants import get_hermes_home

    safe = re.sub(r"[^\w\-]", "_", ANRAK_MCP_SERVER).strip("_")[:128] or "default"
    base = Path(get_hermes_home()) / "mcp-tokens"
    return base / f"{safe}.json", base / f"{safe}.client.json", base / f"{safe}.meta.json"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_private(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".jolly.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _mcp_resource_url() -> str:
    """The connector URL, sent as the RFC 8707 ``resource`` on refresh."""
    try:
        from hermes_cli.config import load_config

        server = (load_config().get("mcp_servers") or {}).get(ANRAK_MCP_SERVER) or {}
        return str(server.get("url") or "").strip()
    except Exception:
        return ""


_REFRESH_LOCK = threading.Lock()


def _refresh_oauth_tokens(tokens: dict) -> dict | None:
    tokens_path, client_path, meta_path = _token_paths()
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    meta = _read_json(meta_path) or {}
    client = _read_json(client_path) or {}
    token_endpoint = str(meta.get("token_endpoint") or "").strip()
    client_id = str(client.get("client_id") or "").strip()
    if not (refresh_token and token_endpoint and client_id):
        return None

    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    resource = _mcp_resource_url()
    if resource:
        form["resource"] = resource
    auth = None
    secret = str(client.get("client_secret") or "")
    if secret:
        if client.get("token_endpoint_auth_method") == "client_secret_basic":
            auth = (client_id, secret)
        else:
            form["client_secret"] = secret

    try:
        resp = httpx.post(token_endpoint, data=form, auth=auth, timeout=15.0)
        if resp.status_code >= 400:
            logger.info("Anrak OAuth refresh rejected (%s)", resp.status_code)
            return None
        fresh = resp.json()
    except Exception as exc:
        logger.info("Anrak OAuth refresh failed: %s", exc)
        return None
    if not isinstance(fresh, dict) or not fresh.get("access_token"):
        return None

    merged = {**tokens, **fresh}
    try:
        merged["expires_at"] = time.time() + int(fresh["expires_in"])
    except (KeyError, TypeError, ValueError):
        merged.pop("expires_at", None)
    try:
        _write_json_private(tokens_path, merged)
    except OSError as exc:
        logger.debug("Could not persist refreshed Anrak token: %s", exc)
    return merged


def anrak_oauth_access_token(*, refresh: bool = True) -> str:
    """Current Anrak Legal access token, refreshed when near expiry.

    Returns "" when the user never signed in. A stale token is returned as-is
    when refresh is impossible, so the server's 401 reaches the user.
    """
    tokens_path = _token_paths()[0]
    tokens = _read_json(tokens_path)
    if not tokens or not tokens.get("access_token"):
        return ""

    def _fresh(t: dict) -> bool:
        exp = t.get("expires_at")
        try:
            return exp is None or float(exp) - time.time() > 120
        except (TypeError, ValueError):
            return True

    if _fresh(tokens) or not refresh:
        return str(tokens["access_token"])
    with _REFRESH_LOCK:
        tokens = _read_json(tokens_path) or tokens  # another thread may have refreshed
        if _fresh(tokens):
            return str(tokens["access_token"])
        refreshed = _refresh_oauth_tokens(tokens)
        return str((refreshed or tokens).get("access_token") or "")


def configured_api_key() -> str:
    try:
        from hermes_cli.config import get_env_value

        return (get_env_value(API_KEY_ENV) or "").strip()
    except Exception:
        return (os.environ.get(API_KEY_ENV) or "").strip()


# ── Wire adaptation ───────────────────────────────────────────────────────

# tool_call id -> jolly.route. Process-lifetime; a lost route (restart
# mid-tool-loop) degrades to a fresh request with the full history.
_ROUTES: "OrderedDict[str, str]" = OrderedDict()
_ROUTES_MAX = 1024
_ROUTES_LOCK = threading.Lock()


def _remember_route(tool_call_ids: list[str], route: Any) -> None:
    if not route or not tool_call_ids:
        return
    with _ROUTES_LOCK:
        for tc_id in tool_call_ids:
            _ROUTES[tc_id] = route
            _ROUTES.move_to_end(tc_id)
        while len(_ROUTES) > _ROUTES_MAX:
            _ROUTES.popitem(last=False)


def _route_for(messages: Any) -> Any:
    """Route of the tool conversation the request continues, if any."""
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        ids = [tc.get("id") for tc in (msg.get("tool_calls") or []) if isinstance(tc, dict)]
        with _ROUTES_LOCK:
            for tc_id in ids:
                if tc_id in _ROUTES:
                    return _ROUTES[tc_id]
        return None  # only the latest assistant turn can be continued
    return None


def _jolly_route(payload: dict) -> Any:
    jolly = payload.get("jolly")
    if isinstance(jolly, dict) and jolly.get("route"):
        return jolly["route"]
    for choice in payload.get("choices") or []:
        msg = (choice or {}).get("message") or (choice or {}).get("delta") or {}
        jolly = msg.get("jolly") if isinstance(msg, dict) else None
        if isinstance(jolly, dict) and jolly.get("route"):
            return jolly["route"]
    return None


def _tool_call_ids(completion: dict) -> list[str]:
    ids = []
    for choice in completion.get("choices") or []:
        for tc in ((choice or {}).get("message") or {}).get("tool_calls") or []:
            if isinstance(tc, dict) and tc.get("id"):
                ids.append(tc["id"])
    return ids


def _find_completion(payload: Any) -> dict | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("choices"), list) and payload["choices"]:
        return payload
    for key in ("result", "response", "completion", "output", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("choices"), list) and nested["choices"]:
            if "jolly" in payload and "jolly" not in nested:
                nested = {**nested, "jolly": payload["jolly"]}
            return nested
    return None


def _task_info(payload: dict) -> dict:
    task = payload.get("task")
    return task if isinstance(task, dict) else payload


def _task_text(payload: dict) -> str | None:
    info = _task_info(payload)
    for source in (info, payload):
        for key in ("content", "output", "text", "draft", "result", "answer"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _synthesize_completion(text: str, payload: dict) -> dict:
    info = _task_info(payload)
    completion = {
        "id": str(info.get("id") or payload.get("id") or f"jolly-{int(time.time() * 1000)}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": JOLLY_MODEL,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
    }
    if isinstance(payload.get("usage"), dict):
        completion["usage"] = payload["usage"]
    if "jolly" in payload:
        completion["jolly"] = payload["jolly"]
    return completion


def _error_response(status: int, message: str, request: httpx.Request) -> httpx.Response:
    body = {"error": {"message": message, "type": "jolly_task_error", "code": status}}
    return httpx.Response(
        status, headers={"content-type": "application/json"}, content=json.dumps(body).encode(), request=request
    )


def _completion_to_sse(completion: dict) -> bytes:
    base = {
        "id": completion.get("id") or "jolly",
        "object": "chat.completion.chunk",
        "created": completion.get("created") or int(time.time()),
        "model": completion.get("model") or JOLLY_MODEL,
    }
    chunks = []
    finish = "stop"
    for idx, choice in enumerate(completion.get("choices") or []):
        msg = (choice or {}).get("message") or {}
        finish = (choice or {}).get("finish_reason") or finish
        delta: dict[str, Any] = {"role": "assistant"}
        for key in ("content", "reasoning", "reasoning_content"):
            if msg.get(key):
                delta[key] = msg[key]
        tool_calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = (tc or {}).get("function") or {}
            tool_calls.append(
                {
                    "index": i,
                    "id": tc.get("id"),
                    "type": tc.get("type") or "function",
                    "function": {"name": fn.get("name"), "arguments": fn.get("arguments") or ""},
                }
            )
        if tool_calls:
            delta["tool_calls"] = tool_calls
        chunks.append({**base, "choices": [{"index": idx, "delta": delta, "finish_reason": None}]})
    final: dict[str, Any] = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
    if completion.get("usage"):
        final["usage"] = completion["usage"]
    chunks.append(final)
    out = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
    return out + b"data: [DONE]\n\n"


class _RouteSniffingStream(httpx.SyncByteStream):
    """Pass SSE bytes through untouched while collecting tool-call ids + route."""

    def __init__(self, inner: httpx.SyncByteStream):
        self._inner = inner

    def __iter__(self) -> Iterator[bytes]:
        buffer = b""
        ids: list[str] = []
        route = None
        try:
            for chunk in self._inner:
                yield chunk
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == b"[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    route = _jolly_route(event) or route
                    for choice in event.get("choices") or []:
                        for tc in ((choice or {}).get("delta") or {}).get("tool_calls") or []:
                            if isinstance(tc, dict) and tc.get("id"):
                                ids.append(tc["id"])
        finally:
            _remember_route(ids, route)

    def close(self) -> None:
        self._inner.close()


class JollyTransport(httpx.BaseTransport):
    """httpx transport adapting Jolly's task/route protocol to chat-completions."""

    def __init__(self, inner: httpx.BaseTransport):
        self._inner = inner

    def close(self) -> None:
        self._inner.close()

    # -- auth ----------------------------------------------------------------

    @staticmethod
    def _headers(request: httpx.Request) -> dict[str, str]:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in {"content-length", "host"}}
        if not configured_api_key():
            # Managed access: always send the freshest Anrak sign-in token
            # (the one resolved at client construction may have expired).
            token = anrak_oauth_access_token()
            if token:
                headers["authorization"] = f"Bearer {token}"
        return headers

    # -- request handling ----------------------------------------------------

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not request.url.path.rstrip("/").endswith("/chat/completions"):
            return self._inner.handle_request(
                httpx.Request(request.method, request.url, headers=self._headers(request),
                              content=request.read(), extensions=request.extensions)
            )

        try:
            body = json.loads(request.read() or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._inner.handle_request(request)

        route = _route_for(body.get("messages"))
        if route is not None and "task" not in body:
            body["task"] = route
        wants_stream = bool(body.get("stream"))

        response = self._send(request, body)
        if wants_stream and response.status_code in (400, 422) and b"stream" in response.content.lower():
            # Server refused streaming — ask again without it and re-emit as SSE.
            body = {k: v for k, v in body.items() if k not in {"stream", "stream_options"}}
            response = self._send(request, body)

        if response.status_code >= 400:
            return response
        if "text/event-stream" in response.headers.get("content-type", ""):
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=_RouteSniffingStream(response.stream),
                request=request,
                extensions=response.extensions,
            )

        response.read()
        try:
            payload = response.json()
        except ValueError:
            return response
        result = self._settle(request, payload)
        if isinstance(result, httpx.Response):
            return result
        _remember_route(_tool_call_ids(result), _jolly_route(result) or _jolly_route(payload))

        if wants_stream:
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=_completion_to_sse(result), request=request
            )
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=json.dumps(result).encode(), request=request
        )

    def _send(self, request: httpx.Request, body: dict) -> httpx.Response:
        outgoing = httpx.Request(
            "POST",
            request.url,
            headers=self._headers(request),
            content=json.dumps(body).encode(),
            extensions=request.extensions,
        )
        response = self._inner.handle_request(outgoing)
        if response.status_code >= 400 or "text/event-stream" not in response.headers.get("content-type", ""):
            response.read()
        return response

    def _settle(self, request: httpx.Request, payload: Any) -> dict | httpx.Response:
        """Resolve a response to a finished completion, polling drafting tasks."""
        completion = _find_completion(payload)
        if completion is not None:
            return completion
        if not isinstance(payload, dict):
            return _error_response(502, "Jolly returned an unexpected response.", request)

        info = _task_info(payload)
        poll_url = info.get("poll_url") or payload.get("poll_url")
        if not poll_url:
            text = _task_text(payload)
            if text is not None:
                return _synthesize_completion(text, payload)
            return _error_response(502, "Jolly returned neither a completion nor a task.", request)

        poll_url = urljoin(str(request.url), str(poll_url))
        deadline = time.monotonic() + _POLL_TIMEOUT_S
        interval = _POLL_INTERVAL_S
        last = payload
        while True:
            status = str(_task_info(last).get("status") or "").lower()
            if status in _TASK_FAILED:
                err = _task_info(last).get("error") or last.get("error") or f"Jolly task {status}."
                if isinstance(err, dict):
                    err = err.get("message") or json.dumps(err)
                return _error_response(502, str(err), request)
            if status in _TASK_DONE:
                text = _task_text(last)
                if text is not None:
                    return _synthesize_completion(text, last)
            if time.monotonic() >= deadline:
                return _error_response(504, "Jolly drafting task did not finish in time.", request)

            time.sleep(interval)
            interval = min(interval * 1.5, _POLL_INTERVAL_MAX_S)
            poll = self._inner.handle_request(
                httpx.Request("GET", poll_url, headers=self._headers(request), extensions=request.extensions)
            )
            poll.read()
            if poll.status_code == 202 or (poll.status_code == 404 and status in ("", "queued", "pending")):
                continue  # still queued / not yet visible
            if poll.status_code >= 400:
                return poll
            try:
                last = poll.json()
            except ValueError:
                return _error_response(502, "Jolly task poll returned invalid JSON.", request)
            completion = _find_completion(last)
            if completion is not None:
                return completion
            if not isinstance(last, dict):
                return _error_response(502, "Jolly task poll returned an unexpected response.", request)
            # Carry the route forward: the first response may be the only one naming it.
            if "jolly" not in last and "jolly" in payload:
                last = {**last, "jolly": payload["jolly"]}


# ── Profile ───────────────────────────────────────────────────────────────


class AnrakJollyProfile(ProviderProfile):
    def resolve_fallback_secret(self) -> str:
        return anrak_oauth_access_token(refresh=False)

    def wrap_http_transport(self, transport: Any) -> Any:
        return JollyTransport(transport)

    def fetch_models(self, *, api_key: str | None = None, timeout: float = 8.0) -> list[str] | None:
        # No public catalog endpoint; Jolly is a single routed model.
        return [JOLLY_MODEL]


anrak = AnrakJollyProfile(
    name="anrak",
    aliases=("anrak-jolly", "anraklegal", "jolly"),
    display_name="Anrak Jolly",
    description="Anrak Jolly — AnrakLegal's multimodal legal model (anraklegal/jolly)",
    signup_url="https://developers.anrak.legal/developers/jolly",
    env_vars=(API_KEY_ENV, "ANRAK_JOLLY_BASE_URL"),
    base_url=JOLLY_BASE_URL,
    auth_type="api_key",
    supports_health_check=False,
    default_aux_model=JOLLY_MODEL,
    fallback_models=(JOLLY_MODEL,),
)

register_provider(anrak)
