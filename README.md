# Copilot Gate: OpenAI-compatible API for Microsoft Copilot

An unofficial API bridge that turns Microsoft Copilot into an OpenAI-compatible endpoint. Supports both **consumer** (personal) and **enterprise** (Microsoft 365 Copilot) accounts.

## Features

- **Enterprise support** — works with Microsoft 365 Copilot (Sydney protocol), including advanced models
- **Consumer support** — works with personal Copilot accounts (Cloudflare protocol)
- **OpenAI-compatible** — drop-in replacement for the OpenAI Chat Completions API
- **Streaming** — token-by-token output via SSE
- **Multi-turn conversations** — continue threads by `conversation_id`
- **Docker-ready** — pre-built image on GHCR, just mount your session

> **Unofficial project.** Not affiliated with or endorsed by Microsoft.

---

## Requirements

- Python 3.9+ (for local dev) or Docker (for deployment)
- A Microsoft account (personal or enterprise/work)

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/kedaya2025/copilot-gate.git
cd copilot-gate
```

### 2. Sign in

Sign in with your Microsoft account. For **enterprise** (Microsoft 365 Copilot):

```bash
pip install -r requirements.txt
playwright install chromium

COPILOT_URL=https://m365.cloud.microsoft python -m copilot login
```

For **consumer** (personal) Copilot:

```bash
python -m copilot login
```

A browser opens — sign in, and it closes automatically once detected. The session is saved under `session/` (git-ignored).

### 3. Run the server

**Local:**

```bash
python app.py
# -> http://127.0.0.1:8000
```

**Docker (using pre-built image):**

```bash
# Copy and edit .env
cp .env.example .env

docker compose up -d
```

### 4. Use it

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")

resp = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

Or with `curl`:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello!"}]}'
```

---

## Configuration

All configuration is via environment variables (or `.env` file):

| Variable | Default | Description |
| --- | --- | --- |
| `COPILOT_URL` | `https://copilot.microsoft.com` | Consumer URL. Set to `https://m365.cloud.microsoft` for enterprise. |
| `HOST` | `127.0.0.1` | Server bind address. |
| `PORT` | `8000` | Server port. |
| `RATE_LIMIT_RPM` | `12` | Requests per minute. `0` disables. |
| `RATE_LIMIT_BURST` | `4` | Max back-to-back requests before pacing. |

---

## API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` | Chat (supports `"stream": true` and `"conversation_id"`) |
| `GET` | `/v1/models` | Lists the `copilot` model |

---

## Docker Deployment

### Production (pre-built image)

The CI builds and pushes the image to `ghcr.io/kedaya2025/copilot-gate:latest` on every push to `main`.

```bash
# On your server:
cd deploy
cp ../.env.example .env  # edit as needed
docker compose up -d
```

The `deploy/docker-compose.yml` uses the pre-built image — no local build needed.

### Development (local build)

```bash
docker compose up --build
```

### Session management

Sign in **on a machine with a display** (the login opens a visible browser). The `session/` directory persists cookies and tokens. For Docker, mount it as a volume:

```bash
docker run --rm -p 8000:8000 -v "$(pwd)/session:/app/session" \
  -e COPILOT_URL=https://m365.cloud.microsoft \
  ghcr.io/kedaya2025/copilot-gate:latest
```

For enterprise accounts, clearance does **not** expire (no Cloudflare), so the session is long-lived. For consumer accounts, Cloudflare clearance expires (~30 min); re-run `python -m copilot login` to refresh.

---

## How It Works

### Enterprise (Microsoft 365 Copilot)

Uses the **Sydney protocol** over WebSocket (`wss://substrate.office.com/m365Copilot/Chathub/...`). The driver handles the SignalR-style framing (record separator `\x1e`), handshake, and message types (`type:1` updates with `writeAtCursor`, `type:2` conversation ID, `type:3` completion). No Cloudflare clearance needed.

### Consumer (personal Copilot)

Uses the consumer Copilot WebSocket protocol (`wss://copilot.microsoft.com/c/api/chat`). Requires Cloudflare `cf_clearance` cookie, earned via a browser-based challenge. The driver solves proof-of-work challenges in-process.

---

## Project Layout

| Path | What it does |
| --- | --- |
| `copilot/` | Core library: client, auth, browser sign-in, HTTP drivers |
| `copilot/config.py` | Central URL/configuration (consumer vs enterprise) |
| `copilot/driver.py` | Consumer Copilot driver (Cloudflare protocol) |
| `copilot/sydney.py` | Enterprise Copilot driver (Sydney protocol) |
| `server/` | FastAPI OpenAI-compatible server |
| `app.py` | Server entry point |
| `.github/workflows/` | CI: builds and pushes Docker image to GHCR |
| `deploy/` | Production docker-compose using pre-built image |

---

## License

Released under the [MIT License](LICENSE). As this is an unofficial project, you remain responsible for complying with Microsoft's terms of service.
