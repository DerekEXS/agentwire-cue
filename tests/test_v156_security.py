"""v1.5.6 RED: security review follow-up.

Covers:
- /admin/peers redacts uuid and url.
- /admin/peers caches reachability probes with a short TTL.
- A2A listener refuses inbound requests when bound to a non-loopback
  address without an auth token.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import make_mocked_request

from agentwire_cue.core import admin_api
from agentwire_cue.core.a2a_client import A2AListener


def test_admin_peers_redacts_uuid_and_url():
    host = SimpleNamespace(
        a2a_client=SimpleNamespace(aliases={
            'Pawly': {
                'uuid': '628b49d96dcde97a',
                'url': 'http://47.109.25.89:18800/secret/path',
            }
        })
    )
    request = make_mocked_request(
        'GET', '/admin/peers',
        app={'host': host, 'admin_token': 'secret'},
        headers={'Authorization': 'Bearer secret'},
    )

    response = asyncio.run(admin_api.handle_admin_peers(request))
    body = json.loads(response.text)
    peer = body['peers']['Pawly']

    assert response.status == 200
    assert peer['uuid'].startswith('628b49')
    assert '...' in peer['uuid']
    assert '628b49d96dcde97a' not in peer['uuid']
    assert '47.109.25.89' in peer['url']
    assert '/secret/path' not in peer['url']


@pytest.mark.asyncio
async def test_admin_peers_caches_reachability(monkeypatch):
    host = SimpleNamespace(
        a2a_client=SimpleNamespace(aliases={
            'Pawly': {'uuid': 'uuid', 'url': 'http://example.invalid:18800'}
        })
    )
    calls = {'count': 0}

    async def fake_probe(url, timeout_s=1.0):
        calls['count'] += 1
        return True

    monkeypatch.setattr(admin_api, '_probe_peer_reachable', fake_probe)
    request = make_mocked_request(
        'GET', '/admin/peers',
        app={'host': host, 'admin_token': 'secret'},
        headers={'Authorization': 'Bearer secret'},
    )
    first = await admin_api.handle_admin_peers(request)
    second = await admin_api.handle_admin_peers(request)

    assert first.status == 200
    assert second.status == 200
    assert calls['count'] == 1, f"expected cached probe to be reused, got {calls['count']}"


@pytest.mark.asyncio
async def test_inbound_refused_when_non_loopback_without_token():
    listener = A2AListener(host='0.0.0.0', port=18801, auth_token=None)

    async def fake_request_json():
        return {
            'jsonrpc': '2.0', 'id': 1,
            'params': {'message': {'type': 'A2A_MESSAGE', 'text': 'hi'}}
        }

    request = make_mocked_request(
        'POST', '/a2a/inbound',
        headers={},
        payload=SimpleNamespace(),
    )
    request.json = fake_request_json

    response = await listener._handle_inbound(request)

    assert response.status == 403
    body = json.loads(response.text)
    assert body['error'] == 'bound_without_token'


def test_loopback_listener_without_token_still_accepts():
    listener = A2AListener(host='127.0.0.1', port=18801, auth_token=None)
    assert listener.allow_inbound_without_token is True
