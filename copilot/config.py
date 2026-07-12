"""Central URL configuration for the Copilot bridge.

Supports both **consumer** Copilot (``copilot.microsoft.com``) and **enterprise**
Microsoft 365 Copilot (``m365.cloud.microsoft``). Set the env var
``COPILOT_URL`` to switch::

    # consumer (default)
    python app.py

    # enterprise / work account
    COPILOT_URL=https://m365.cloud.microsoft python app.py

The API path prefix can also be overridden with ``COPILOT_API_PATH`` (default
``/c/api`` for consumer; the enterprise endpoint may use ``/chat/api`` or the
same ``/c/api`` — the bridge auto-detects from the browser during login and
stores the actual WebSocket URL in ``session/token.json``).
"""

import os
from urllib.parse import urlparse

# --- Base URL ---------------------------------------------------------------

# Consumer: https://copilot.microsoft.com
# Enterprise: https://m365.cloud.microsoft
DEFAULT_BASE_URL = "https://copilot.microsoft.com"
BASE_URL = os.environ.get("COPILOT_URL", DEFAULT_BASE_URL).rstrip("/")

# --- Chat page URL (where the browser navigates) ----------------------------

# Consumer Copilot loads at the root; enterprise loads at /chat/.
if "copilot.microsoft.com" in BASE_URL:
    CHAT_PAGE_URL = f"{BASE_URL}/"
else:
    CHAT_PAGE_URL = f"{BASE_URL}/chat/"

# --- API path prefix --------------------------------------------------------

# Consumer uses /c/api/* ; enterprise may use the same or /chat/api/*.
# The WS URL captured during login overrides this, so it's only a fallback.
API_PATH = os.environ.get("COPILOT_API_PATH", "/c/api").rstrip("/")

# --- Derived URLs -----------------------------------------------------------

# WebSocket endpoint for the chat socket.
# The driver prefers a URL captured live during login (stored in token.json);
# this is the fallback when no captured URL is available.
CHAT_WEBSOCKET_URL = f"wss://{urlparse(BASE_URL).hostname}{API_PATH}/chat?api-version=2"

# REST endpoints for conversation management and attachments.
CONVERSATION_URL = f"{BASE_URL}{API_PATH}/conversations"
ATTACHMENTS_URL = f"{BASE_URL}{API_PATH}/attachments"

# --- Cookie domain filter ---------------------------------------------------

# Domains we accept cookies from. Consumer cookies live on *.microsoft.com;
# enterprise cookies may also live on *.cloud.microsoft.
COOKIE_DOMAINS = ("microsoft.com", "cloud.microsoft")


def is_enterprise() -> bool:
    """True when configured for an enterprise (Microsoft 365 Copilot) account."""
    return "copilot.microsoft.com" not in BASE_URL
