"""History client for AgentWire-Cue v1.4.3.

Fetches history JSON-RPC endpoints from an AgentWire CORE gateway.
Caches results for 30s (per peer, per query shape) to keep expression
evaluation fast and avoid hammering the gateway.

Cue plugin expressions and triggers can then read history via
`history.peer("Pawly").last_n_rounds(5)` (see expression.py).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import RLock
from typing import Any

log = logging.getLogger("agentwire.history")


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

    def _resolve_peer_token(self, peer: str) -> str:
        """v1.6.1: resolve per-peer A2A token from alias metadata.

        Priority: token_file > token_env > token (literal) > default self.token.
        """
        if not self._aliases:
            return self.token
        meta = self._aliases.get(peer)
        if meta is None:
            return self.token
        token_file = meta.get('token_file')
        if token_file:
            try:
                with open(token_file, 'r') as fh:
                    val = fh.read().strip()
                if val:
                    return val
            except (OSError, PermissionError):
                pass
        token_env = meta.get('token_env')
        if token_env:
            val = os.environ.get(token_env)
            if val:
                return val
        token_literal = meta.get('token')
        if token_literal:
            return token_literal
        return self.token

    def _rpc(self, method: str, params: dict, token: str | None = None) -> dict:
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
                "Authorization": f"Bearer {token or self.token}",
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
        v1.6.1: per-peer token used when configured on the alias.
        """
        target = self._resolve_alias(peer)
        key = ("list", target, limit, since_round)
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        peer_token = self._resolve_peer_token(peer) if self._aliases else self.token
        result = self._rpc("messages/list", {
            "peer_uuid": target,
            "limit": limit,
            "since_round": since_round,
        }, token=peer_token)
        msgs = result.get("messages", [])
        self._set_cached(key, msgs)
        return msgs

    def list_peers(self) -> list[dict]:
        """v2.0.2: use ListTasks instead of deprecated messages/peers.

        ListTasks has no peerId filter, so we paginate all tasks and
        derive peer identity from task.context_id prefix (e.g. 'pawly::123').
        Falls back to configured aliases when ListTasks is unavailable.
        """
        key = ("peers",)
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        peers_map: dict[str, dict] = {}

        # v2.0.2: primary — ListTasks with pagination
        try:
            page_token = ""
            while True:
                params = {"page_size": 100}
                if page_token:
                    params["page_token"] = page_token
                result = self._rpc("ListTasks", params)
                tasks = result.get("tasks", [])
                for task in tasks:
                    ctx_id = task.get("contextId", "") or ""
                    # contextId format: 'peer_name::uuid' or just 'uuid'
                    if "::" in ctx_id:
                        peer_name = ctx_id.split("::", 1)[0]
                    else:
                        # No peer prefix — skip (task from unknown peer)
                        continue
                    if peer_name not in peers_map:
                        peers_map[peer_name] = {
                            "name": peer_name,
                            "uuid": task.get("id", "")[:8],
                            "last_round": 0,
                        }
                    # Track highest round seen
                    task_id = task.get("id", "")
                    try:
                        round_num = int(task_id, 16) if task_id.startswith("0x") else 0
                        # Use task_id hash as proxy for ordering
                        peers_map[peer_name]["last_round"] = max(
                            peers_map[peer_name].get("last_round", 0),
                            round_num,
                        )
                    except (ValueError, TypeError):
                        pass
                # Pagination
                next_token = result.get("next_page_token", "") or result.get("nextPageToken", "")
                if not next_token:
                    break
                page_token = next_token
                if len(peers_map) >= 50:  # safety cap
                    break
        except Exception as e:
            log.warning("list_peers ListTasks failed (%s), falling back to aliases", e)
            # v2.0.2: fallback — synthesize from configured aliases
            if self._aliases:
                for alias_name, alias_meta in self._aliases.items():
                    peers_map[alias_name] = {
                        "name": alias_name,
                        "uuid": alias_meta.get("uuid", alias_name[:8]),
                        "last_round": 0,
                    }
            else:
                # Last resort: return empty (no peers known)
                pass

        peers = list(peers_map.values())
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
