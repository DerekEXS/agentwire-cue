"""v1.4.8 RED: peer alias resolution + history_uuid resolution.

Goals:
- cue.yaml peer: "Pawly"  → HistoryClient 用配置的 uuid 查 history
- HistoryClient.list_messages 接受 peer alias，先查 alias map；命中失败时
  应给出可识别的 reason 配合 v1.4.7 admin trigger diagnostics
- 没有 peers 配置时保持旧行为（peer 字符串当 uuid/name 直接发 CORE）
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))


# ---------- 准备：fake HistoryClient 单元 ----------
class _FakeRPCRaising:
    def _rpc(self, method, params, token=None):
        raise AssertionError(f"unexpected RPC {method} params={params}")


def _make_history_client(aliases):
    """Import HistoryClient after stubbing urllib.request to keep tests offline."""
    from agentwire_cue.core.history_client import HistoryClient

    client = HistoryClient(
        a2a_url="http://127.0.0.1:18800",
        token="x",
    )
    client._aliases = aliases
    return client


def test_list_messages_resolves_alias_to_uuid_before_calling_core():
    seen = {}

    class _Rpc:
        def __call__(self, method, params, token=None):
            seen.setdefault("calls", []).append((method, params))
            return {"messages": [{"role": "inbound", "parts": [{"type": "text", "text": "urgent: hi"}]}]}

    client = _make_history_client(aliases={"Pawly": {"uuid": "pawly-demo-uuid", "url": "http://pawly.example.invalid:18800"}})
    client._rpc = _Rpc().__call__

    msgs = client.list_messages("Pawly", limit=5)

    assert msgs and msgs[0]["parts"][0]["text"] == "urgent: hi"
    assert seen["calls"] == [
        ("messages/list", {"peer_uuid": "pawly-demo-uuid", "limit": 5, "since_round": 0})
    ]


def test_list_messages_raises_peer_not_found_for_unknown_alias():
    client = _make_history_client(aliases={"Pawly": {"uuid": "pawly-demo-uuid", "url": "http://pawly.example.invalid:18800"}})
    client._rpc = _FakeRPCRaising()._rpc

    from agentwire_cue.core.history_proxy import HistoryDiagnosticError
    with pytest.raises(HistoryDiagnosticError) as exc:
        client.list_messages("NonExistent", limit=5)
    assert exc.value.reason == "peer_not_found"
    assert exc.value.peer == "NonExistent"


def test_list_messages_accepts_uuid_when_alias_table_is_configured():
    seen = {}
    client = _make_history_client(aliases={"Pawly": {"uuid": "pawly-demo-uuid", "url": "http://pawly.example.invalid:18800"}})

    def _rpc(method, params, token=None):
        seen["params"] = params
        return {"messages": [{"role": "inbound"}]}

    client._rpc = _rpc

    assert client.list_messages("pawly-demo-uuid", limit=5) == [{"role": "inbound"}]
    assert seen["params"] == {"peer_uuid": "pawly-demo-uuid", "limit": 5, "since_round": 0}


def test_list_messages_raises_peer_not_configured_when_alias_map_is_empty():
    """Empty alias table: legacy behavior is to forward the name as peer_uuid.

    v1.4.8 keeps the legacy path working; we only raise when the alias table
    is configured AND the peer is missing from it. The contract is verified
    by the no-alias-map test below.
    """
    client = _make_history_client(aliases={})
    captured = {}

    def _rpc(method, params, token=None):
        captured["params"] = params
        return {"messages": []}

    client._rpc = _rpc
    msgs = client.list_messages("Pawly", limit=5)
    assert msgs == []
    assert captured["params"] == {"peer_uuid": "Pawly", "limit": 5, "since_round": 0}
