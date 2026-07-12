"""Server configuration — shared constants."""

import os
import secrets

# The single model id this bridge advertises (Copilot has no model selector).
MODEL_NAME = "copilot"

# API key for authenticating requests. Set via the ``API_KEY`` env var.
# When set, all /v1/* endpoints require ``Authorization: Bearer <key>``.
# When empty (default), auth is disabled (backward compatible).
# If the var is set to "auto", a random key is generated on startup and
# printed to stderr.
_api_key_raw = os.environ.get("API_KEY", "")
if _api_key_raw.lower() == "auto":
    API_KEY = f"sk-copilot-{secrets.token_urlsafe(32)}"
    print(f"[copilot] Generated API key: {API_KEY}", flush=True)
else:
    API_KEY = _api_key_raw

# Self-imposed rate limit (Copilot publishes none). Tune to whatever ceiling the
# probe in tests/ratelimit.py shows your account tolerates.
#   RATE_LIMIT_RPM   requests/minute the bridge will accept; 0 disables limiting.
#   RATE_LIMIT_BURST max requests allowed back-to-back before pacing kicks in.
# Default 12 rpm sits safely below the ~15 rpm where one account starts seeing
# upstream 502s, so the limiter only bites when callers try to exceed that.
RATE_LIMIT_RPM = float(os.environ.get("RATE_LIMIT_RPM", "12"))  # 12 rpm ≈ 5s per call
RATE_LIMIT_BURST = int(os.environ.get("RATE_LIMIT_BURST", "4"))
