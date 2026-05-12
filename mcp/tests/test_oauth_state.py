"""Tests for oauth_state.py — bounded stores, expiry, and StoreFullError."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from oauth_state import (
    MAX_ENTRIES_PER_DICT,
    StoreFullError,
    _is_expired,
    cleanup_and_pop,
    cleanup_and_store,
)


def make_entry(expires_at: object = None) -> dict:
    entry: dict = {"data": "test"}
    if expires_at is not None:
        entry["expires_at"] = expires_at
    return entry


class TestIsExpired:
    def test_no_expires_at_never_expires(self) -> None:
        entry = make_entry()
        now_ts = time.time()
        now_dt = datetime.now(UTC)
        assert _is_expired(entry, now_ts, now_dt) is False

    def test_future_datetime_not_expired(self) -> None:
        entry = make_entry(expires_at=datetime.now(UTC) + timedelta(hours=1))
        now_ts = time.time()
        now_dt = datetime.now(UTC)
        assert _is_expired(entry, now_ts, now_dt) is False

    def test_past_datetime_expired(self) -> None:
        entry = make_entry(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        now_ts = time.time()
        now_dt = datetime.now(UTC)
        assert _is_expired(entry, now_ts, now_dt) is True

    def test_future_float_not_expired(self) -> None:
        entry = make_entry(expires_at=time.time() + 3600)
        now_ts = time.time()
        now_dt = datetime.now(UTC)
        assert _is_expired(entry, now_ts, now_dt) is False

    def test_past_float_expired(self) -> None:
        entry = make_entry(expires_at=time.time() - 1)
        now_ts = time.time()
        now_dt = datetime.now(UTC)
        assert _is_expired(entry, now_ts, now_dt) is True


class TestCleanupAndPop:
    def test_pop_existing_key(self) -> None:
        store: dict = {}
        cleanup_and_store(store, "k1", make_entry())
        result = cleanup_and_pop(store, "k1")
        assert result is not None
        assert result["data"] == "test"
        assert "k1" not in store

    def test_pop_missing_key_returns_none(self) -> None:
        store: dict = {}
        assert cleanup_and_pop(store, "missing") is None

    def test_pop_evicts_expired_entries(self) -> None:
        store: dict = {}
        cleanup_and_store(store, "k1", make_entry(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        cleanup_and_store(store, "k2", make_entry())
        # Pop k2 — should also evict expired k1
        result = cleanup_and_pop(store, "k2")
        assert result is not None
        assert "k1" not in store


class TestStoreFullError:
    def test_raises_when_store_full(self) -> None:
        store: dict = {}
        for i in range(MAX_ENTRIES_PER_DICT):
            store[str(i)] = make_entry()
        with pytest.raises(StoreFullError):
            cleanup_and_store(store, "overflow", make_entry())

    def test_accepts_new_entry_after_all_expire(self) -> None:
        store: dict = {}
        # Fill store with expired entries
        for i in range(MAX_ENTRIES_PER_DICT):
            store[str(i)] = make_entry(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        # Should succeed — expired entries are cleaned before the cap check
        cleanup_and_store(store, "new_key", make_entry())
        assert "new_key" in store
