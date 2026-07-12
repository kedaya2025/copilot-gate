"""Server configuration — shared constants."""

import os
import secrets

# The single model id this bridge advertises (Copilot has no model selector).
MODEL_NAME = "copilot"

# ---------------------------------------------------------------------------
# API Key (bootstrap / backward-compatible)
# ---------------------------------------------------------------------------
# The API_KEY env var serves as a "bootstrap" key that always works.
# Additional keys can be created at runtime via the admin API (see /admin/keys)
# and are persisted to a file — no restart needed.
# When set to "auto", a random key is generated on startup and printed to stderr.
# When empty, only keystore-managed keys are valid (or auth is disabled if
# neither API_KEY nor any keystore key exists).
_api_key_raw = os.environ.get("API_KEY", "")
if _api_key_raw.lower() == "auto":
    API_KEY = f"sk-copilot-{secrets.token_urlsafe(32)}"
    print(f"[copilot] Generated bootstrap API key: {API_KEY}", flush=True)
else:
    API_KEY = _api_key_raw

# ---------------------------------------------------------------------------
# Admin Key
# ---------------------------------------------------------------------------
# The ADMIN_KEY env var protects the /admin/* management endpoints.
# When set to "auto", a random admin key is generated on startup and printed.
# When empty, admin endpoints are disabled entirely (returns 404).
_admin_key_raw = os.environ.get("ADMIN_KEY", "")
if _admin_key_raw.lower() == "auto":
    ADMIN_KEY = f"admin-{secrets.token_urlsafe(32)}"
    print(f"[copilot] Generated admin key: {ADMIN_KEY}", flush=True)
else:
    ADMIN_KEY = _admin_key_raw

# Self-imposed rate limit (Copilot publishes none). Tune to whatever ceiling the
# probe in tests/ratelimit.py shows your account tolerates.
#   RATE_LIMIT_RPM   requests/minute the bridge will accept; 0 disables limiting.
#   RATE_LIMIT_BURST max requests allowed back-to-back before pacing kicks in.
# Default 12 rpm sits safely below the ~15 rpm where one account starts seeing
# upstream 502s, so the limiter only bites when callers try to exceed that.
RATE_LIMIT_RPM = float(os.environ.get("RATE_LIMIT_RPM", "12"))  # 12 rpm ≈ 5s per call
RATE_LIMIT_BURST = int(os.environ.get("RATE_LIMIT_BURST", "4"))
