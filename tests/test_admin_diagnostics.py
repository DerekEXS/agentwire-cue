"""v1.5.1 P2 RED tests: admin diagnostics endpoints.

Contract:
- ``GET /admin/status`` returns a JSON document with cue_version,
  uptime_seconds, and a ``plugins`` map keyed by plugin name. Each
  entry exposes the current ``state`` and the latest trigger
  bookkeeping fields (``last_trigger_at``, ``last_match``,
  ``last_reason``, ``last_details``). Missing values stay ``null``.
- ``GET /admin/peers`` returns peer alias config (uuid, url) and the
  reachable status of each peer.
- ``GET /admin/plugins`` returns a small plugin roster.
- All endpoints require a valid Bearer token.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

from agentwire_cue.core.admin_api import (
    handle_admin_peers,
    handle_admin_plugins,
    handle_admin_status,
)


def _make_host(plugins: dict, peers: dict | None = None, started_at_ms: int = 1_000) -> SimpleNamespace:
    return SimpleNamespace(
        plugins=plugins,
        admin_token="secret",
        started_at_ms=started_at_ms,
        a2a_url="http://127.0.0.1:18800",
        a2a_listener_port=18801,
        a2a_client=SimpleNamespace(aliases=peers or {}),
        draining=False,
    )


def _make_plugin(
    *,
    name: str = "owner-alert",
    version: str = "1.5.0",
    current_state: str = "watching",
    last_trigger_at: int | None = None,
    last_match: bool | None = None,
    last_reason: str | None = None,
    last_details: dict | None = None,
) -> SimpleNamespace:
    statechart = SimpleNamespace(current_state=current_state)
    return SimpleNamespace(
        name=name,
        version=version,
        api_version="agentwire/v1.2",
        statechart=statechart,
        peers={},
        last_trigger_at=last_trigger_at,
        last_match=last_match,
        last_reason=last_reason,
        last_details=last_details,
    )


@pytest.mark.asyncio
async def test_admin_status_returns_plugins_and_uptime():
    plugin = _make_plugin(
        last_trigger_at=2_000,
        last_match=True,
        last_reason=None,
        last_details=None,
    )
    host = _make_host({"owner-alert": plugin})
    request = make_mocked_request(
        "GET", "/admin/status",
        app={"host": host, "admin_token": "secret"},
        headers={"Authorization": "Bearer secret"},
    )

    response = await handle_admin_status(request)
    body = json.loads(response.text)

    assert response.status == 200
    assert "uptime_seconds" in body
    assert body["cue_version"]
    assert "owner-alert" in body["plugins"]
    entry = body["plugins"]["owner-alert"]
    assert entry["state"] == "watching"
    assert entry["last_trigger_at"] == 2_000
    assert entry["last_match"] is True
    assert entry["last_reason"] is None


@pytest.mark.asyncio
async def test_admin_status_never_triggered_plugin_returns_nulls():
    plugin = _make_plugin()
    host = _make_host({"owner-alert": plugin})
    request = make_mocked_request(
        "GET", "/admin/status",
        app={"host": host, "admin_token": "secret"},
        headers={"Authorization": "Bearer secret"},
    )

    response = await handle_admin_status(request)
    body = json.loads(response.text)

    entry = body["plugins"]["owner-alert"]
    assert entry["last_trigger_at"] is None
    assert entry["last_match"] is None
    assert entry["last_reason"] == "never_triggered"


@pytest.mark.asyncio
async def test_admin_status_requires_auth():
    host = _make_host({})
    request = make_mocked_request(
        "GET", "/admin/status",
        app={"host": host, "admin_token": "secret"},
        headers={},
    )

    response = await handle_admin_status(request)
    assert response.status == 401


@pytest.mark.asyncio
async def test_admin_peers_lists_aliases():
    peers = {
        "Pawly": {"uuid": "demo", "url": "http://demo.invalid:18800"},
    }
    host = _make_host({}, peers=peers)
    request = make_mocked_request(
        "GET", "/admin/peers",
        app={"host": host, "admin_token": "secret"},
        headers={"Authorization": "Bearer secret"},
    )

    response = await handle_admin_peers(request)
    body = json.loads(response.text)

    assert response.status == 200
    assert "Pawly" in body["peers"]
    entry = body["peers"]["Pawly"]
    # v1.5.6: short demo uuid (<= 6 chars) is left intact, url is
    # redacted to scheme + host + port only.
    assert entry["uuid"] == "demo"
    assert entry["url"] == "http://demo.invalid:18800"
    assert "reachable" in entry


@pytest.mark.asyncio
async def test_admin_peers_requires_auth():
    host = _make_host({})
    request = make_mocked_request(
        "GET", "/admin/peers",
        app={"host": host, "admin_token": "secret"},
        headers={},
    )
    response = await handle_admin_peers(request)
    assert response.status == 401


@pytest.mark.asyncio
async def test_admin_plugins_lists_loaded_plugins():
    host = _make_host({
        "owner-alert": _make_plugin(name="owner-alert"),
        "video-publish": _make_plugin(name="video-publish"),
    })
    request = make_mocked_request(
        "GET", "/admin/plugins",
        app={"host": host, "admin_token": "secret"},
        headers={"Authorization": "Bearer secret"},
    )

    response = await handle_admin_plugins(request)
    body = json.loads(response.text)

    assert response.status == 200
    assert body["count"] == 2
    assert set(body["plugins"]) == {"owner-alert", "video-publish"}


@pytest.mark.asyncio
async def test_admin_plugins_requires_auth():
    host = _make_host({})
    request = make_mocked_request(
        "GET", "/admin/plugins",
        app={"host": host, "admin_token": "secret"},
        headers={},
    )
    response = await handle_admin_plugins(request)
    assert response.status == 401
