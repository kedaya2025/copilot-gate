"""File-based API key store with hot-reload support.

Keys are persisted to a JSON file so they survive container restarts.
The store is thread-safe and can be updated at runtime via admin endpoints
without rebuilding or restarting the container.
"""

import json
import os
import secrets
import threading
import time
from typing import Optional

# Default path for the key store file. Can be overridden via DATA_DIR env var.
_DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.getcwd(), "data"))
_KEYSTORE_FILE = os.path.join(_DATA_DIR, "api_keys.json")

_lock = threading.Lock()


def _ensure_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


def _load() -> list[dict]:
    """Load keys from the JSON file. Returns an empty list if file doesn't exist."""
    try:
        with open(_KEYSTORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def _save(keys: list[dict]):
    """Persist keys to the JSON file."""
    _ensure_dir()
    tmp = _KEYSTORE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _KEYSTORE_FILE)  # atomic on POSIX


def list_keys() -> list[dict]:
    """Return all active keys (revoked keys excluded)."""
    with _lock:
        return [k for k in _load() if not k.get("revoked")]


def list_all_keys() -> list[dict]:
    """Return all keys including revoked ones (for admin view)."""
    with _lock:
        return _load()


def generate_key(name: str = "") -> dict:
    """Generate a new API key, persist it, and return the full key record."""
    key_id = f"key_{secrets.token_hex(4)}"
    raw_key = f"sk-copilot-{secrets.token_urlsafe(32)}"
    record = {
        "id": key_id,
        "key": raw_key,
        "name": name or f"Key created {time.strftime('%Y-%m-%d %H:%M')}",
        "created_at": int(time.time()),
        "revoked": False,
    }
    with _lock:
        keys = _load()
        keys.append(record)
        _save(keys)
    return record


def revoke_key(key_id: str) -> bool:
    """Revoke a key by its id. Returns True if found and revoked."""
    with _lock:
        keys = _load()
        for k in keys:
            if k["id"] == key_id:
                if k.get("revoked"):
                    return False  # already revoked
                k["revoked"] = True
                k["revoked_at"] = int(time.time())
                _save(keys)
                return True
        return False


def is_valid_key(raw_key: str, bootstrap_key: str = "") -> bool:
    """Check if a raw key string is valid.

    A key is valid if it matches:
    1. The bootstrap key from the API_KEY env var (if set), or
    2. Any active (non-revoked) key in the keystore file.
    """
    if not raw_key:
        return False
    if bootstrap_key and raw_key == bootstrap_key:
        return True
    with _lock:
        for k in _load():
            if not k.get("revoked") and k.get("key") == raw_key:
                return True
    return False


def mask_key(key: str) -> str:
    """Mask a key for display, showing only the first 12 and last 4 characters."""
    if len(key) <= 20:
        return key[:8] + "..." if len(key) > 8 else "***"
    return key[:12] + "..." + key[-4:]
