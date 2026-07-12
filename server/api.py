"""FastAPI app wiring Copilot onto the OpenAI Chat Completions API."""

import threading
import time
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from copilot import CopilotClient
from copilot.driver import ClearanceRequired

from .config import ADMIN_KEY, API_KEY, MODEL_NAME, RATE_LIMIT_BURST, RATE_LIMIT_RPM
from . import keystore
from .openai_format import (
    completion_response,
    new_id,
    sse_event,
    stream_chunk,
)
from .prompt import messages_to_prompt
from .ratelimit import TokenBucket
from .schemas import ChatCompletionRequest

app = FastAPI(title="Copilot OpenAI-compatible API", version="1.3.0")

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _extract_bearer(authorization: Optional[str]) -> str:
    """Extract the raw key from an Authorization header."""
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return authorization  # accept raw key


def _check_auth(authorization: Optional[str]) -> bool:
    """Validate the Bearer token.

    A key is valid if it matches:
    1. The bootstrap key from the API_KEY env var (if set), or
    2. Any active key in the SQLite keystore.
    If neither API_KEY nor any keystore key exists, auth is disabled.
    """
    raw_key = _extract_bearer(authorization)
    if not API_KEY and not keystore.list_keys(active_only=True):
        return True  # auth disabled (no keys configured at all)
    return keystore.is_valid_key(raw_key, bootstrap_key=API_KEY)


def _check_admin(authorization: Optional[str]) -> bool:
    """Validate the admin token for /admin/* endpoints."""
    if not ADMIN_KEY:
        return False  # admin endpoints disabled
    raw_key = _extract_bearer(authorization)
    return raw_key == ADMIN_KEY


def _unauthorized():
    return JSONResponse(
        status_code=401,
        content={"error": {
            "message": "Invalid API key. Set the 'Authorization: Bearer <key>' header.",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }},
    )


def _forbidden():
    return JSONResponse(
        status_code=403,
        content={"error": {
            "message": "Admin key required for this endpoint.",
            "type": "invalid_request_error",
            "code": "admin_required",
        }},
    )


def _not_found():
    return JSONResponse(
        status_code=404,
        content={"error": {
            "message": "Not found.",
            "type": "invalid_request_error",
        }},
    )


def _admin_gate(authorization: Optional[str]):
    """Common guard for admin endpoints. Returns a response if access denied, None if allowed."""
    if not ADMIN_KEY:
        return _not_found()
    if not _check_admin(authorization):
        return _forbidden()
    return None


# Server runs headless and must never pop a visible browser mid-request. With
# both recovery passes disabled, an expired clearance surfaces immediately as a
# 503 (see ClearanceRequired handling below) so an operator can re-clear out of
# band (`python -m copilot login`). Headless auto-solve is intentionally off:
# it's unreliable on low-trust egress and a failed pass can wedge the session.
client = CopilotClient(interactive_clear=False, headless_clear=False)

_CLEARANCE_HELP = (
    "Cloudflare clearance expired and could not be refreshed headlessly. "
    "Re-clear in a browser: run `python -m copilot login` (or `python tests/diagnostic.py`) "
    "and pass the 'verify you're human' check, then retry."
)

# Self-imposed rate limit on top of the concurrency lock below: this caps
# requests-per-minute, the lock caps requests-in-flight. See server/ratelimit.py.
_rate_limiter = TokenBucket(RATE_LIMIT_RPM, RATE_LIMIT_BURST)


def _rate_limited_response():
    """Spend a token; return an OpenAI-shaped 429 if none left, else ``None``."""
    allowed, wait = _rate_limiter.try_acquire()
    if allowed:
        return None
    secs = max(1, round(wait))
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(secs)},
        content={"error": {
            "message": (
                f"Rate limit exceeded (>{RATE_LIMIT_RPM:g} req/min). "
                f"Retry in {secs}s."
            ),
            "type": "rate_limit_error",
            "code": "rate_limit_exceeded",
        }},
    )

# Copilot's per-account chat socket doesn't tolerate concurrent conversations
# from one process (parallel requests error out or hang). This server bridges a
# single signed-in account, so we serialize upstream calls: concurrent HTTP
# requests queue here and run one at a time. Predictable, at the cost of
# parallelism — fine for a personal bridge.
_upstream_lock = threading.Lock()


def _stream(prompt: str, model: str, conversation_id=None):
    """Yield OpenAI ``chat.completion.chunk`` SSE events for ``prompt``.

    ``conversation_id`` continues an existing Copilot thread; ``None`` starts a
    fresh one (its id is emitted on the final chunk).
    """
    cid = new_id()
    created = int(time.time())
    try:
        with _upstream_lock:  # one upstream chat at a time (released on disconnect)
            yield sse_event(stream_chunk(cid, created, model, {"role": "assistant"}))
            stream = client.stream(prompt, conversation_id=conversation_id)
            for piece in stream:
                if isinstance(piece, str) and piece:
                    yield sse_event(stream_chunk(cid, created, model, {"content": piece}))
            # Copilot's conversation id is known once the stream has run; emit it
            # on the final chunk so callers can track the upstream thread.
            yield sse_event(
                stream_chunk(
                    cid, created, model, {}, finish="stop",
                    conversation_id=stream.conversation_id,
                )
            )
    except ClearanceRequired:
        yield sse_event(
            stream_chunk(cid, created, model, {"content": f"\n[error: {_CLEARANCE_HELP}]"}, finish="error")
        )
    except Exception as exc:  # surface errors to the client instead of hanging
        yield sse_event(
            stream_chunk(cid, created, model, {"content": f"\n[error: {exc}]"}, finish="error")
        )
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/models")
def list_models(authorization: Optional[str] = Header(None)):
    if not _check_auth(authorization):
        return _unauthorized()
    return {
        "object": "list",
        "data": [
            {"id": MODEL_NAME, "object": "model", "created": 0, "owned_by": "microsoft"}
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest, authorization: Optional[str] = Header(None)):
    if not _check_auth(authorization):
        return _unauthorized()
    prompt = messages_to_prompt(req.messages)
    if not prompt.strip():
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "no text content in messages", "type": "invalid_request_error"}},
        )
    model = req.model or MODEL_NAME

    # Enforce the per-minute ceiling before touching the upstream lock, so excess
    # callers get a fast 429 instead of piling up behind the serialized queue.
    limited = _rate_limited_response()
    if limited is not None:
        return limited

    if req.stream:
        return StreamingResponse(
            _stream(prompt, model, req.conversation_id), media_type="text/event-stream"
        )

    try:
        with _upstream_lock:  # serialize: one upstream chat at a time
            reply = client.chat(prompt, conversation_id=req.conversation_id)
    except ClearanceRequired:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": _CLEARANCE_HELP, "type": "clearance_required"}},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": {"message": str(exc), "type": "upstream_error"}},
        )
    return completion_response(reply.text, model, reply.conversation_id)


# ---------------------------------------------------------------------------
# Admin endpoints — hot key management (no restart required)
# ---------------------------------------------------------------------------

class CreateKeyRequest(BaseModel):
    name: str = ""


@app.post("/admin/keys")
def create_key(req: CreateKeyRequest, authorization: Optional[str] = Header(None)):
    """Generate a new API key. The full key is returned once; it is not retrievable later."""
    denied = _admin_gate(authorization)
    if denied:
        return denied
    record = keystore.generate_key(name=req.name)
    return {
        "id": record["id"],
        "key": record["key"],
        "name": record["name"],
        "status": record["status"],
        "created_at": record["created_at"],
        "message": "Save this key now — it will not be shown in full again.",
    }


@app.get("/admin/keys")
def list_keys(authorization: Optional[str] = Header(None)):
    """List all API keys (masked). Includes status, usage stats, and timestamps."""
    denied = _admin_gate(authorization)
    if denied:
        return denied
    keys = keystore.list_keys(active_only=False)
    return {
        "count": len(keys),
        "keys": [
            {
                "id": k["id"],
                "name": k["name"],
                "key": keystore.mask_key(k["key"]),
                "status": k["status"],
                "created_at": k["created_at"],
                "revoked_at": k.get("revoked_at"),
                "last_used_at": k.get("last_used_at"),
                "usage_count": k.get("usage_count", 0),
            }
            for k in keys
        ],
        "bootstrap_key_configured": bool(API_KEY),
    }


@app.get("/admin/keys/{key_id}")
def get_key(key_id: str, authorization: Optional[str] = Header(None)):
    """Get details of a single key by its id (key value is masked)."""
    denied = _admin_gate(authorization)
    if denied:
        return denied
    k = keystore.get_key(key_id)
    if not k:
        return _not_found()
    return {
        "id": k["id"],
        "name": k["name"],
        "key": keystore.mask_key(k["key"]),
        "status": k["status"],
        "created_at": k["created_at"],
        "revoked_at": k.get("revoked_at"),
        "last_used_at": k.get("last_used_at"),
        "usage_count": k.get("usage_count", 0),
    }


@app.delete("/admin/keys/{key_id}")
def revoke_key(key_id: str, authorization: Optional[str] = Header(None)):
    """Revoke an API key by its id."""
    denied = _admin_gate(authorization)
    if denied:
        return denied
    revoked = keystore.revoke_key(key_id)
    if not revoked:
        return JSONResponse(
            status_code=404,
            content={"error": {"message": f"Key '{key_id}' not found or already revoked."}},
        )
    return {"message": f"Key '{key_id}' revoked successfully.", "id": key_id}


@app.get("/admin/stats")
def key_stats(authorization: Optional[str] = Header(None)):
    """Return aggregate statistics about API keys."""
    denied = _admin_gate(authorization)
    if denied:
        return denied
    return keystore.key_stats()


# ---------------------------------------------------------------------------
# Root / health
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    has_keys = bool(API_KEY) or bool(keystore.list_keys(active_only=True))
    return {
        "service": "Copilot OpenAI-compatible API",
        "version": "1.3.0",
        "auth": "enabled" if has_keys else "disabled",
        "endpoints": ["/v1/models", "/v1/chat/completions"],
        "admin": "enabled" if ADMIN_KEY else "disabled",
        "admin_endpoints": ["/admin/keys", "/admin/keys/{id}", "/admin/stats"] if ADMIN_KEY else [],
    }
