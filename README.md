# Copilot Gate

[![CI](https://github.com/kedaya2025/copilot-gate/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/kedaya2025/copilot-gate/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue)](https://github.com/kedaya2025/copilot-gate/pkgs/container/copilot-gate)

An OpenAI-compatible API gateway for [Microsoft Copilot](https://copilot.microsoft.com). Bridges Copilot's chat backend onto the standard OpenAI Chat Completions API, so any OpenAI-compatible client works as a drop-in.

Supports both **personal** (consumer) and **enterprise** (Microsoft 365 Copilot) accounts.

> **Unofficial project.** Not affiliated with or endorsed by Microsoft. Use responsibly and within Microsoft's terms of service.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [API Reference](#api-reference)
- [Usage Examples](#usage-examples)
- [Rate Limiting & Concurrency](#rate-limiting--concurrency)
- [Session Management](#session-management)
- [Troubleshooting](#troubleshooting)
- [Project Layout](#project-layout)
- [License](#license)

---

## Overview

Copilot Gate turns a signed-in Microsoft Copilot account into a local API server that speaks the OpenAI format. No API key, no credits, no paid plan — it uses the web chat backend you already have access to.

**Key capabilities:**

- **Dual-mode** — works with personal Copilot (`copilot.microsoft.com`) and enterprise Microsoft 365 Copilot (`m365.cloud.microsoft`)
- **OpenAI-compatible** — `POST /v1/chat/completions` with streaming, `GET /v1/models`
- **Multi-turn conversations** — continue threads via `conversation_id`
- **Token streaming** — SSE (`text/event-stream`) for incremental output
- **Docker-native** — pre-built image on GHCR, CI/CD via GitHub Actions

---

## Architecture

The project uses two different protocols depending on the account type:

### Enterprise (Microsoft 365 Copilot)

```
Client ──HTTP──▶ FastAPI ──▶ CopilotClient ──▶ SydneyDriver
                                                   │
                                                   ▼
                                    wss://substrate.office.com/m365Copilot/Chathub
                                    (Sydney/Bing Chat protocol, SignalR framing)
```

- **Protocol:** Sydney (SignalR-style, record separator `\x1e`)
- **Auth:** JWT with `aud=https://substrate.office.com/sydney`, scope `sydney.readwrite`
- **Cloudflare:** Not involved — no clearance needed
- **Token lifetime:** Long-lived ( enterprise sessions don't expire like consumer Cloudflare clearance)

### Consumer (personal Copilot)

```
Client ──HTTP──▶ FastAPI ──▶ CopilotClient ──▶ Copilot (driver)
                                                   │
                                                   ▼
                                    wss://copilot.microsoft.com/c/api/chat
                                    (consumer protocol, Cloudflare-gated)
```

- **Protocol:** Consumer Copilot WebSocket (`send` → `challenge` → `appendText` → `done`)
- **Auth:** MSAL token (scope `ChatAI.ReadWrite`) + `cf_clearance` cookie
- **Cloudflare:** Required — clearance earned via browser, expires ~30 min
- **Challenge solving:** Proof-of-work (hashcash, copilot) solved in-process

The server auto-detects the protocol from the saved session (`session/token.json` field `protocol`: `"sydney"` or `"copilot"`).

---

## Requirements

- **Python 3.9+** (for local development)
- **Docker** (for containerized deployment)
- A **Microsoft account** — personal or enterprise/work

---

## Installation

### From source

```bash
git clone https://github.com/kedaya2025/copilot-gate.git
cd copilot-gate

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\Activate.ps1

pip install -r requirements.txt
playwright install chromium
```

### Sign in

The login step opens a visible browser for interactive Microsoft sign-in. The session is saved to `session/` (git-ignored) and reused automatically.

**Enterprise (Microsoft 365 Copilot):**

```bash
COPILOT_URL=https://m365.cloud.microsoft python -m copilot login
```

**Consumer (personal Copilot):**

```bash
python -m copilot login
```

The browser closes itself once sign-in is detected. For consumer accounts, it also sends a warm-up message to earn Cloudflare clearance. For enterprise accounts, it captures the WebSocket URL and access token.

---

## Configuration

All configuration is via environment variables or a `.env` file (see [`.env.example`](.env.example)):

| Variable | Default | Description |
| --- | --- | --- |
| `COPILOT_URL` | `https://copilot.microsoft.com` | Base URL. Set to `https://m365.cloud.microsoft` for enterprise. |
| `HOST` | `127.0.0.1` | Server bind address. Use `0.0.0.0` in Docker. |
| `PORT` | `8000` | Server port. |
| `RATE_LIMIT_RPM` | `12` | Max requests per minute. `0` disables limiting. |
| `RATE_LIMIT_BURST` | `4` | Max back-to-back requests before pacing kicks in. |

---

## Deployment

### Option A: Docker with pre-built image (recommended for production)

The CI pipeline builds and pushes the image to GHCR on every push to `main`:

```bash
ghcr.io/kedaya2025/copilot-gate:latest
```

On your server:

```bash
git clone https://github.com/kedaya2025/copilot-gate.git
cd copilot-gate/deploy

# Create .env
cp ../.env.example .env
# Edit .env: set COPILOT_URL, rate limits, etc.

# Start
docker compose up -d
```

The `deploy/docker-compose.yml` pulls the pre-built image — no local build required. It mounts `./session` for credentials and joins `proxy-network` (create it first: `docker network create proxy-network`).

### Option B: Docker with local build (for development)

```bash
cp .env.example .env
docker compose up --build -d
```

This uses the root [`docker-compose.yml`](docker-compose.yml) which builds from the `Dockerfile`.

### Option C: Run directly

```bash
python app.py
# → Copilot OpenAI-compatible API on http://127.0.0.1:8000
```

---

## API Reference

### `POST /v1/chat/completions`

OpenAI Chat Completions compatible.

**Request body:**

```json
{
  "model": "copilot",
  "messages": [{"role": "user", "content": "Hello!"}],
  "stream": false,
  "conversation_id": null
}
```

| Field | Type | Description |
| --- | --- | --- |
| `model` | string | Model id (always `"copilot"`). |
| `messages` | array | OpenAI-format messages. Flattened to a single prompt. |
| `stream` | boolean | If `true`, returns SSE stream. Default `false`. |
| `conversation_id` | string | Optional. Continue an existing conversation. |

**Response (non-streaming):**

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "copilot",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Hello!"},
    "finish_reason": "stop"
  }],
  "conversation_id": "abc123-..."
}
```

**Response (streaming):** Standard OpenAI SSE format with `data:` chunks, terminated by `data: [DONE]`.

### `GET /v1/models`

```json
{
  "object": "list",
  "data": [{"id": "copilot", "object": "model", "created": 0, "owned_by": "microsoft"}]
}
```

---

## Usage Examples

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")

# Non-streaming
resp = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Explain quantum computing in one sentence."}],
)
print(resp.choices[0].message.content)

# Streaming
stream = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Write a haiku about the sea."}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### curl

```bash
# Non-streaming
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello!"}]}'

# Streaming
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Hello!"}],"stream":true}'
```

### Python (direct library)

```python
from copilot import CopilotClient

client = CopilotClient()

# Full reply
reply = client.chat("My name is Alice. Remember it.")
print(reply.text, reply.conversation_id)

# Continue the conversation
reply2 = client.chat("What's my name?", reply.conversation_id)
print(reply2.text)

# Stream
for chunk in client.stream("Tell me a joke"):
    print(chunk, end="", flush=True)
```

More examples in [`examples/`](examples/).

---

## Rate Limiting & Concurrency

The server bridges a **single** Copilot account. Copilot's chat socket does not tolerate concurrent conversations from one process, so the server **serializes** upstream calls — parallel HTTP requests queue behind a lock and run one at a time.

On top of the concurrency lock, a token-bucket rate limiter caps requests per minute:

- **`RATE_LIMIT_RPM`** (default `12`): max requests/minute. `0` disables.
- **`RATE_LIMIT_BURST`** (default `4`): max back-to-back requests before pacing.

Excess requests get a `429` + `Retry-After`. Both `429` (bridge limit) and occasional `502` (upstream hiccup) are transient — retry with exponential backoff.

---

## Session Management

The `session/` directory stores:

- `token.json` — access token, cookies, WebSocket URL, protocol type
- `profile/` — persistent Chromium profile (cookies, sign-in state)

**Enterprise accounts:** Sessions are long-lived. No Cloudflare clearance to expire. Re-login only if the token fully expires (rare).

**Consumer accounts:** Cloudflare `cf_clearance` expires after ~30 minutes. The server returns `503` (`type: "clearance_required"`) when it expires. Re-run `python -m copilot login` on a machine with a display to refresh.

**Docker:** Always sign in on a host with a display first, then mount `session/` into the container. The container handles token refresh headlessly but cannot perform interactive sign-in.

---

## Troubleshooting

### Enterprise: `No enterprise WebSocket URL found`

The login didn't capture the WebSocket URL. Re-run:

```bash
COPILOT_URL=https://m365.cloud.microsoft python -m copilot login
```

Make sure you actually sign in (not just open the page) and that a chat message is sent during the warm-up.

### Consumer: `503 clearance_required`

Cloudflare clearance expired. Re-run `python -m copilot login` on a machine with a display.

### Consumer: `chat-service-unavailable`

Copilot is geo-restricted in some regions. Use a proxy in a supported region:

```bash
python -m copilot login --proxy http://user:pass@host:port
```

### Docker: `network proxy-network not found`

Create the external network first:

```bash
docker network create proxy-network
```

### Diagnostic tool

For consumer accounts, the diagnostic refreshes the session and writes a report:

```bash
python tests/diagnostic.py                # browser capture + report
python tests/diagnostic.py --report-only  # headless/VPS: report only
```

---

## Project Layout

| Path | Description |
| --- | --- |
| [`copilot/`](copilot/) | Core library: client, auth, browser sign-in, HTTP drivers |
| [`copilot/config.py`](copilot/config.py) | Central URL/configuration (consumer vs enterprise) |
| [`copilot/driver.py`](copilot/driver.py) | Consumer Copilot driver (Cloudflare protocol) |
| [`copilot/sydney.py`](copilot/sydney.py) | Enterprise Copilot driver (Sydney protocol) |
| [`copilot/browser.py`](copilot/browser.py) | Playwright sign-in and token capture |
| [`server/`](server/) | FastAPI OpenAI-compatible server |
| [`app.py`](app.py) | Server entry point |
| [`.github/workflows/`](.github/workflows/) | CI: build and push Docker image to GHCR |
| [`deploy/`](deploy/) | Production docker-compose using pre-built GHCR image |
| [`examples/`](examples/) | Runnable examples for every feature |
| [`tests/`](tests/) | Stress test, rate limit probe, diagnostic tool |

---

## License

[MIT License](LICENSE). This is an unofficial project; you remain responsible for complying with Microsoft's terms of service.
