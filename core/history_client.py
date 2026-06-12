"""History client for AgentWire-Cue v1.4.3.

Fetches history JSON-RPC endpoints from an AgentWire CORE gateway.
Caches results for 30s (per peer, per query shape) to keep expression
evaluation fast and avoid hammering the gateway.

Cue plugin expressions and triggers can then read history via
`history.peer("Pawly").last_n_rounds(5)` (see expression.py).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from threading import RLock
from typing import Any


class HistoryClient:
    """Caches per-peer history lookups for a TTL window.

    Thread-safe. Cache key is (method, peer_uuid_or_name, limit).
    """

    def __init__(
        self,
        a2a_url: str,
        token: str,
        ttl_seconds: int = 30,
        timeout_seconds: int = 5,
    ):
        self.a2a_url = a2a_url.rstrip("/")
        self.token = token
        self.ttl = ttl_seconds
        self.timeout = timeout_seconds
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._lock = RLock()
        # v1.4.8: optional peer alias table. Keyed by alias name (e.g. "Pawly")
        # to a dict containing at least {"uuid": "...", "url": "..."}.
        # When empty, peer strings are forwarded to CORE as-is (legacy behavior).
        self._aliases: dict[str, dict] = {}

    def set_aliases(self, aliases: dict[str, dict]) -> None:
        """v1.4.8: install peer alias table. Invalidates the cache so previously
        cached (wrong-uuid) history lookups are re-fetched.
        """
        with self._lock:
            self._aliases = dict(aliases)
            self._cache.clear()

    def _resolve_alias(self, peer: str) -> str:
        """v1.4.8: map an alias to its CORE peer uuid. Falls back to the
        raw `peer` string when the alias table is empty or the peer is not
        configured. Raises HistoryDiagnosticError when the alias table is set
        but the peer is unknown — this is the v1.4.7 trigger diagnostic path.
        """
        if not self._aliases:
            return peer
        meta = self._aliases.get(peer)
        if meta is None:
            for alias_meta in self._aliases.values():
                if alias_meta.get("uuid") == peer:
                    return peer
            from .history_proxy import HistoryDiagnosticError
            raise HistoryDiagnosticError(
                "peer_not_found",
                f"peer {peer!r} not in configured aliases",
                peer=peer,
            )
        return meta["uuid"]

    def _rpc(self, method: str, params: dict) -> dict:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.a2a_url}/a2a/jsonrpc",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            raise RuntimeError(f"history RPC {method} failed: {e}") from e
        if "error" in resp:
            raise RuntimeError(f"history RPC {method} error: {resp['error']}")
        return resp.get("result", {})

    def _get_cached(self, key: tuple) -> Any | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.time() - ts > self.ttl:
                del self._cache[key]
                return None
            return value

    def _set_cached(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._cache[key] = (time.time(), value)

    def list_messages(
        self, peer: str, limit: int = 5, since_round: int = 0
    ) -> list[dict]:
        """Return recent messages for `peer` (uuid or display name).

        v1.4.8: when a peer alias table is configured, the alias is resolved
        to its uuid before contacting CORE.
        """
        target = self._resolve_alias(peer)
        key = ("list", target, limit, since_round)
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        result = self._rpc("messages/list", {
            "peer_uuid": target,
            "limit": limit,
            "since_round": since_round,
        })
        msgs = result.get("messages", [])
        self._set_cached(key, msgs)
        return msgs

    def list_peers(self) -> list[dict]:
        key = ("peers",)
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        result = self._rpc("messages/peers", {})
        peers = result.get("peers", [])
        self._set_cached(key, peers)
        return peers

    def get_round(self, peer: str, round_n: int) -> list[dict]:
        target = self._resolve_alias(peer)
        key = ("get", target, round_n)
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        result = self._rpc("messages/get", {
            "peer_uuid": target,
            "round": round_n,
        })
        msgs = result.get("messages", [])
        self._set_cached(key, msgs)
        return msgs

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
