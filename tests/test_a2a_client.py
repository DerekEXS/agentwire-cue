"""Test suite for v1.3.1 patch 2 commit 3: peer card cache + a2a_client (D4)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agentwire_cue.core.a2a_client import (
    A2AClient,
    FallbackDispatcher,
    PeerCardCache,
    RetryPolicy,
    SendResult,
)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch):
    """Set up sandbox-compatible cache dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cache = tmp_path / ".local/share/agentwire-cue/peers"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


# ---------- D4: PeerCardCache ----------

class TestPeerCardCache:
    @pytest.mark.asyncio
    async def test_lazy_fetch_first_time(self, cache_dir, monkeypatch):
        cache = PeerCardCache(cache_dir)
        # Mock HTTP fetch
        card = {"protocolVersion": "1.0.1", "name": "alice",
                "endpoints": {"inbound": "http://alice:18801/a2a/inbound"}}
        async def fake_fetch(*a, **kw): return card
        with patch.object(PeerCardCache, '_fetch', new=fake_fetch):
            result = await cache.get("alice", "http://alice:18801")
        assert result == card

    @pytest.mark.asyncio
    async def test_cache_hit_within_ttl(self, cache_dir):
        cache = PeerCardCache(cache_dir, ttl_ms=600_000)
        # Pre-populate cache file
        card = {"name": "alice", "endpoints": {"inbound": "http://alice:18801/a2a/inbound"}}
        cache_path = cache_dir / "alice.json"
        cache_path.write_text(json.dumps({
            "peer_id": "alice",
            "fetched_at_ms": __import__('time').time() * 1000,  # now
            "ttl_ms": 600_000,
            "card": card,
        }))
        # Should hit disk cache without HTTP fetch
        async def fail_fetch(*a, **kw):
            raise AssertionError("should not HTTP fetch on cache hit")
        with patch.object(PeerCardCache, '_fetch', new=fail_fetch):
            result = await cache.get("alice", "http://alice:18801")
        assert result == card

    @pytest.mark.asyncio
    async def test_cache_stale_refetch(self, cache_dir):
        cache = PeerCardCache(cache_dir, ttl_ms=100)  # 100ms TTL
        # Pre-populate stale cache (fetched long ago)
        card_old = {"name": "alice-old", "endpoints": {}}
        card_new = {"name": "alice-new", "endpoints": {}}
        cache_path = cache_dir / "alice.json"
        cache_path.write_text(json.dumps({
            "peer_id": "alice",
            "fetched_at_ms": 0,  # long ago
            "ttl_ms": 100,
            "card": card_old,
        }))
        async def fake_fetch(*a, **kw): return card_new
        with patch.object(PeerCardCache, '_fetch', new=fake_fetch):
            result = await cache.get("alice", "http://alice:18801")
        assert result == card_new

    @pytest.mark.asyncio
    async def test_write_failure_memory_only(self, cache_dir, monkeypatch):
        cache = PeerCardCache(cache_dir)
        card = {"name": "alice", "endpoints": {}}
        # Simulate write failure by making cache_dir unwritable
        cache_path = cache_dir / "alice.json"
        original_write_text = Path.write_text
        def fail_write(self, *a, **kw):
            if str(self) == str(cache_path):
                raise OSError("disk full")
            return original_write_text(self, *a, **kw)
        monkeypatch.setattr(Path, "write_text", fail_write)
        # Should still return card via memory cache
        async def fake_fetch(*a, **kw): return card
        with patch.object(PeerCardCache, '_fetch', new=fake_fetch):
            result = await cache.get("alice", "http://alice:18801")
        assert result == card
        # Memory cache has it
        assert "alice" in cache._memory

    @pytest.mark.asyncio
    async def test_sandbox_enforced(self, cache_dir, monkeypatch):
        cache = PeerCardCache(cache_dir)
        # Mock _fetch to return card; then we try to write but sandbox
        # would reject a non-conforming filename. This test is the inverse:
        # verify that _write_back calls check_surface_path with the right
        # surface and peer_id.
        from agentwire_cue.core.sandbox import SURFACE_PEER
        with patch('agentwire_cue.core.a2a_client.check_surface_path') as mock:
            async def fake_fetch(*a, **kw):
                return {"name": "alice"}
            with patch.object(PeerCardCache, '_fetch', new=fake_fetch):
                await cache.get("alice", "http://alice:18801")
            assert mock.called
            args, kwargs = mock.call_args
            assert kwargs.get('surface') == SURFACE_PEER
            assert kwargs.get('peer_id') == 'alice'

    @pytest.mark.asyncio
    async def test_invalidate(self, cache_dir):
        cache = PeerCardCache(cache_dir)
        card = {"name": "alice"}
        cache._memory["alice"] = {"card": card, "fetched_at_ms": 0, "ttl_ms": 600_000}
        cache_path = cache_dir / "alice.json"
        cache_path.write_text("{}")
        await cache.invalidate("alice")
        assert "alice" not in cache._memory
        assert not cache_path.exists()


# ---------- Retry Policy ----------

class TestRetryPolicy:
    @pytest.mark.asyncio
    async def test_zero_retries(self):
        rp = RetryPolicy(max_retries=0, backoff_ms=100)
        async def send(): return SendResult.FAILED
        result = await rp.execute_with_retry(send)
        assert result == SendResult.FAILED  # No retry, immediate

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        rp = RetryPolicy(max_retries=2, backoff_ms=10)
        calls = [0]
        async def send():
            calls[0] += 1
            if calls[0] < 2:
                return SendResult.FAILED
            return SendResult.SUCCESS
        result = await rp.execute_with_retry(send)
        assert result == SendResult.SUCCESS
        assert calls[0] == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        rp = RetryPolicy(max_retries=2, backoff_ms=10)
        calls = [0]
        async def send():
            calls[0] += 1
            return SendResult.FAILED
        result = await rp.execute_with_retry(send)
        assert result == SendResult.EXHAUSTED
        # max_retries=2 means 1 initial + 2 retries = 3 calls
        assert calls[0] == 3

    @pytest.mark.asyncio
    async def test_permission_denied_not_retried(self):
        rp = RetryPolicy(max_retries=3, backoff_ms=10)
        calls = [0]
        async def send():
            calls[0] += 1
            return SendResult.PERMISSION_DENIED
        result = await rp.execute_with_retry(send)
        assert result == SendResult.PERMISSION_DENIED
        # No retry for PERMISSION_DENIED
        assert calls[0] == 1
