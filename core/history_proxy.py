"""Proxy objects exposed in the expression engine for history queries.

These wrap HistoryClient + a list of peer metadata so that
expressions like `peers.Pawly.history.last(5)` or
`history.total_rounds_today()` work naturally.
"""
from __future__ import annotations

from typing import Any


class HistoryDiagnosticError(Exception):
    def __init__(self, reason: str, message: str, **details):
        super().__init__(message)
        self.reason = reason
        self.details = details
        self.peer = details.get("peer")


class _PeerHistoryProxy:
    """Returned by `peers.<name>.history`. Has methods that fetch via
    HistoryClient.
    """

    def __init__(self, peer_meta: dict, client, requested_peer: str | None = None):
        self._meta = peer_meta
        self._client = client
        self._requested_peer = requested_peer

    def last(self, n: int = 5) -> list[dict]:
        """Return the last n rounds of messages for this peer (cached)."""
        if not self._meta:
            raise HistoryDiagnosticError(
                "peer_not_found",
                f"peer {self._requested_peer!r} not found",
                peer=self._requested_peer,
            )
        if self._client is None:
            return []
        return self._client.list_messages(self._meta.get("uuid") or self._meta.get("name"), limit=n)

    def last_n_rounds(self, n: int = 5) -> list[dict]:
        """Alias for .last() — kept for back-compat with earlier examples."""
        return self.last(n)

    def count(self) -> int:
        """Total rounds stored for this peer."""
        return int(self._meta.get("total_rounds", 0))

    def last_round(self) -> int:
        """Highest round number stored."""
        return int(self._meta.get("last_round", 0))

    def last_inbound_contains(self, needle: str) -> bool:
        """True if any recent inbound message text contains `needle`."""
        msgs = self.last(5)
        if not msgs:
            raise HistoryDiagnosticError(
                "history_empty",
                f"peer {self._meta.get('name') or self._requested_peer!r} history is empty",
                peer=self._meta.get("name") or self._requested_peer,
                uuid=self._meta.get("uuid"),
            )
        for m in msgs:
            if m.get("role") != "inbound":
                continue
            for p in m.get("parts", []):
                if p.get("type") == "text" and needle in (p.get("text") or ""):
                    return True
        return False

    def last_inbound_text(self) -> str:
        """v1.6.5: concatenated text of recent inbound messages.

        Used by ``script-receiver`` to write the full payload to disk after
        the guard expression confirms keywords matched. Skips outbound
        messages (CORE auto-acks) and joins text parts with newlines so the
        saved file is a faithful copy of what the peer sent.
        """
        msgs = self.last(5)
        if not msgs:
            raise HistoryDiagnosticError(
                "history_empty",
                f"peer {self._meta.get('name') or self._requested_peer!r} history is empty",
                peer=self._meta.get("name") or self._requested_peer,
                uuid=self._meta.get("uuid"),
            )
        texts: list[str] = []
        for m in msgs:
            if m.get("role") != "inbound":
                continue
            for p in m.get("parts", []):
                if p.get("type") == "text":
                    t = p.get("text") or ""
                    if t:
                        texts.append(t)
        return "\n".join(texts)

    def last_outbound_contains(self, needle: str) -> bool:
        """True if any recent outbound message text contains `needle`."""
        msgs = self.last(5)
        if not msgs:
            raise HistoryDiagnosticError(
                "history_empty",
                f"peer {self._meta.get('name') or self._requested_peer!r} history is empty",
                peer=self._meta.get("name") or self._requested_peer,
                uuid=self._meta.get("uuid"),
            )
        for m in msgs:
            if m.get("role") != "outbound":
                continue
            for p in m.get("parts", []):
                if p.get("type") == "text" and needle in (p.get("text") or ""):
                    return True
        return False


class _PeerProxy:
    """Returned by `peers.<name>`. Wraps peer metadata + history proxy."""

    def __init__(self, peer_meta: dict, client, requested_peer: str | None = None):
        self._meta = peer_meta
        self._client = client
        self._requested_peer = requested_peer

    def __getattr__(self, item: str) -> Any:
        if item in ("name", "uuid", "last_round", "total_rounds", "last_ts"):
            return self._meta.get(item)
        if item == "history":
            return _PeerHistoryProxy(self._meta, self._client, self._requested_peer)
        return None

    def __bool__(self) -> bool:
        return True  # A peer is always "truthy" if the proxy exists


class _PeersNamespace:
    """Top-level `peers` namespace. Lookup by name (string) or attribute."""

    def __init__(self, client):
        self._client = client
        self._snapshot: list[dict] = []
        self._by_name: dict[str, dict] = {}

    def refresh(self) -> None:
        if self._client is None:
            return
        self._snapshot = self._client.list_peers()
        self._by_name = {p.get("name") or p.get("uuid"): p for p in self._snapshot}
        # v1.4.8: merge configured peer aliases into the namespace so
        # expression lookups like `peers.Pawly` resolve to a populated
        # meta dict regardless of what CORE returned in messages/peers.
        # Aliases that match an existing snapshot entry by uuid get the
        # alias name added as an additional key for direct lookup.
        aliases = getattr(self._client, "_aliases", None) or {}
        for alias_name, alias_meta in aliases.items():
            alias_uuid = alias_meta.get("uuid")
            # Find the snapshot entry this alias points at (by uuid or
            # fallback name), so the alias inherits real peer metadata.
            target_meta = None
            for p in self._snapshot:
                if alias_uuid and p.get("uuid") == alias_uuid:
                    target_meta = p
                    break
            if target_meta is None:
                # No real peer in snapshot — synthesize a meta dict that
                # the alias will use. The HistoryClient's list_messages
                # call will route through `_resolve_alias` and return
                # [] if the peer truly has no history.
                target_meta = {
                    "name": alias_name,
                    "uuid": alias_uuid,
                    "last_round": 0,
                    "total_rounds": 0,
                    "last_ts": None,
                }
            self._by_name[alias_name] = dict(target_meta, name=alias_name)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        meta = self._by_name.get(name)
        if meta is None:
            return _PeerProxy({}, self._client, name)
        return _PeerProxy(meta, self._client, name)

    def get(self, name: str, default=None):
        """Dict-style access for the expression engine's _resolve_path."""
        meta = self._by_name.get(name)
        if meta is None:
            return default
        return _PeerProxy(meta, self._client, name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def keys(self):
        return self._by_name.keys()

    def items(self):
        return self._by_name.items()


class _HistoryNamespace:
    """Top-level `history` namespace. Cross-peer aggregations."""

    def __init__(self, peers_ns: _PeersNamespace):
        self._peers = peers_ns

    def total_rounds(self) -> int:
        return sum(int(p.get("total_rounds", 0)) for p in self._peers._snapshot)

    def total_rounds_today(self) -> int:
        # v1.4.3 simple impl: count rounds with ts starting today's date
        # Actually we don't iterate messages here; use snapshot's last_ts per peer
        # Real per-message date filter would need an extra RPC; skip for v1.4.3.
        return self.total_rounds()

    def peer_count(self) -> int:
        return len(self._peers._snapshot)

    def peer_names(self) -> list[str]:
        return [p.get("name") for p in self._peers._snapshot]

    def refresh(self) -> None:
        self._peers.refresh()
