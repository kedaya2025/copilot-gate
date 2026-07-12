#!/usr/bin/env python3
"""Test script for the SQLite-backed keystore module."""

import os
import sys
import tempfile
import json

# Set up test data directory
_test_dir = tempfile.mkdtemp()
os.environ["DATA_DIR"] = _test_dir
print(f"[test] Using temp dir: {_test_dir}")

# Import keystore directly (bypass server/__init__.py which needs fastapi)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "keystore",
    os.path.join(os.path.dirname(__file__), "..", "server", "keystore.py"),
)
keystore = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(keystore)

def test_create_key():
    """Test key creation."""
    record = keystore.generate_key(name="test-key-1")
    assert record["id"].startswith("key_"), f"Bad id: {record['id']}"
    assert record["key"].startswith("sk-copilot-"), f"Bad key prefix: {record['key']}"
    assert record["name"] == "test-key-1"
    assert record["status"] == "active"
    assert record["created_at"] > 0
    assert record["revoked_at"] is None
    assert record["last_used_at"] is None
    assert record["usage_count"] == 0
    print("[PASS] test_create_key")
    return record

def test_list_keys(record):
    """Test listing keys."""
    # Create a second key
    record2 = keystore.generate_key(name="test-key-2")
    all_keys = keystore.list_keys(active_only=False)
    assert len(all_keys) == 2, f"Expected 2 keys, got {len(all_keys)}"
    active_keys = keystore.list_keys(active_only=True)
    assert len(active_keys) == 2, f"Expected 2 active keys, got {len(active_keys)}"
    print("[PASS] test_list_keys")
    return record2

def test_get_key(record):
    """Test getting a single key."""
    k = keystore.get_key(record["id"])
    assert k is not None, "Key not found"
    assert k["id"] == record["id"]
    assert k["name"] == record["name"]
    assert k["key"] == record["key"]
    assert k["status"] == "active"
    print("[PASS] test_get_key")

def test_is_valid_key(record):
    """Test key validation and usage tracking."""
    # Valid key
    assert keystore.is_valid_key(record["key"]) == True, "Valid key not recognized"
    # Check usage was tracked
    k = keystore.get_key(record["id"])
    assert k["usage_count"] == 1, f"Expected usage_count=1, got {k['usage_count']}"
    assert k["last_used_at"] is not None, "last_used_at not set"
    # Use again
    assert keystore.is_valid_key(record["key"]) == True
    k = keystore.get_key(record["id"])
    assert k["usage_count"] == 2, f"Expected usage_count=2, got {k['usage_count']}"
    # Invalid key
    assert keystore.is_valid_key("sk-copilot-invalid") == False, "Invalid key accepted"
    # Empty key
    assert keystore.is_valid_key("") == False, "Empty key accepted"
    # Bootstrap key
    assert keystore.is_valid_key("bootstrap-test", bootstrap_key="bootstrap-test") == True
    print("[PASS] test_is_valid_key")

def test_revoke_key(record, record2):
    """Test key revocation."""
    # Revoke first key
    assert keystore.revoke_key(record["id"]) == True, "Revoke failed"
    # Revoked key should not be valid
    assert keystore.is_valid_key(record["key"]) == False, "Revoked key still valid"
    # Check status
    k = keystore.get_key(record["id"])
    assert k["status"] == "revoked", f"Expected status='revoked', got '{k['status']}'"
    assert k["revoked_at"] is not None, "revoked_at not set"
    # Double revoke
    assert keystore.revoke_key(record["id"]) == False, "Double revoke should fail"
    # Active list should only have record2
    active = keystore.list_keys(active_only=True)
    assert len(active) == 1, f"Expected 1 active key, got {len(active)}"
    assert active[0]["id"] == record2["id"]
    # All list should still have both
    all_keys = keystore.list_keys(active_only=False)
    assert len(all_keys) == 2, f"Expected 2 total keys, got {len(all_keys)}"
    print("[PASS] test_revoke_key")

def test_key_stats(record, record2):
    """Test stats."""
    stats = keystore.key_stats()
    assert stats["total"] == 2, f"Expected total=2, got {stats['total']}"
    assert stats["active"] == 1, f"Expected active=1, got {stats['active']}"
    assert stats["revoked"] == 1, f"Expected revoked=1, got {stats['revoked']}"
    assert stats["total_requests"] >= 2, f"Expected total_requests>=2, got {stats['total_requests']}"
    print("[PASS] test_key_stats")

def test_mask_key():
    """Test key masking."""
    masked = keystore.mask_key("sk-copilot-abcdefghijklmnopqrstuvwxyz1234567890")
    assert "..." in masked, "Mask should contain ..."
    assert masked.startswith("sk-copilot-"), "Mask should start with prefix"
    assert masked.endswith("7890"), "Mask should end with last 4 chars"
    short = keystore.mask_key("short")
    assert short == "***", f"Short key should be masked, got '{short}'"
    print("[PASS] test_mask_key")

def test_persistence():
    """Test that data persists across module reload."""
    # Generate a key
    record = keystore.generate_key(name="persistence-test")
    # Simulate reload by re-importing the module file
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "keystore_reload",
        os.path.join(os.path.dirname(__file__), "..", "server", "keystore.py"),
    )
    keystore2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(keystore2)
    # Key should still be there
    k = keystore2.get_key(record["id"])
    assert k is not None, "Key not found after reload"
    assert k["name"] == "persistence-test"
    print("[PASS] test_persistence")

def test_db_file_exists():
    """Test that SQLite database file was created."""
    db_path = os.path.join(_test_dir, "api_keys.db")
    assert os.path.exists(db_path), f"DB file not found at {db_path}"
    print(f"[PASS] test_db_file_exists (file: {db_path})")

if __name__ == "__main__":
    print("=" * 60)
    print("Running keystore tests (SQLite backend)")
    print("=" * 60)
    test_db_file_exists()
    record = test_create_key()
    record2 = test_list_keys(record)
    test_get_key(record)
    test_is_valid_key(record)
    test_revoke_key(record, record2)
    test_key_stats(record, record2)
    test_mask_key()
    test_persistence()
    print("=" * 60)
    print(f"All tests passed! (temp dir: {_test_dir})")
    print("=" * 60)
