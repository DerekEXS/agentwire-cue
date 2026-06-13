from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

from agentwire_cue.__main__ import build_parser
from agentwire_cue.core.a2a_client import A2AListener, SendResult
from agentwire_cue.core.host import Host
from agentwire_cue.core.permission import PermissionEnforcer


def test_host_defaults_bind_listeners_to_loopback(tmp_path: Path):
    host = Host(plugin_dir=tmp_path)

    assert host.a2a_listener_host == "127.0.0.1"
    assert host.admin_host == "127.0.0.1"


def test_cli_accepts_explicit_listener_and_admin_hosts():
    parser = build_parser()

    args = parser.parse_args([
        "host",
        "--plugin-dir", "/plugins",
        "--a2a-listener-host", "0.0.0.0",
        "--admin-host", "0.0.0.0",
    ])

    assert args.a2a_listener_host == "0.0.0.0"
    assert args.admin_host == "0.0.0.0"


@pytest.mark.asyncio
async def test_inbound_rejects_missing_bearer_token():
    listener = A2AListener(host="127.0.0.1", port=18801, auth_token="secret")
    request = make_mocked_request(
        "POST",
        "/a2a/inbound",
        headers={},
        payload=SimpleNamespace(),
    )

    response = await listener._handle_inbound(request)
    body = json.loads(response.text)

    assert response.status == 401
    assert body["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_inbound_accepts_matching_bearer_token():
    listener = A2AListener(host="127.0.0.1", port=18801, auth_token="secret")
    seen = []

    async def handler(message):
        seen.append(message)

    listener.register_handler(handler)
    request = make_mocked_request(
        "POST",
        "/a2a/inbound",
        headers={"Authorization": "Bearer secret"},
        payload=SimpleNamespace(),
    )
    async def request_json():
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "params": {"message": {"type": "A2A_MESSAGE", "text": "hi"}},
        }
    request.json = request_json

    response = await listener._handle_inbound(request)

    assert response.status == 200
    assert seen == [{"type": "A2A_MESSAGE", "text": "hi"}]


@pytest.mark.asyncio
async def test_wrap_send_denies_peer_not_in_allowlist(tmp_path: Path):
    host = Host(plugin_dir=tmp_path)
    host.enforcer = PermissionEnforcer()
    host.enforcer.register("plug", {"peers": [{"id": "Pawly", "allow_messages": ["*"]}]})

    class Client:
        async def send_message(self, *args, **kwargs):
            permission_check = kwargs.get("permission_check")
            if permission_check is not None and not permission_check():
                return SendResult.PERMISSION_DENIED
            return SendResult.SUCCESS

    host.a2a_client = Client()
    plugin = SimpleNamespace(name="plug")
    send = host._wrap_send(plugin)

    result = await send("main", "hello")

    assert result == SendResult.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_wrap_send_allows_any_alias_when_peer_allowlist_empty(tmp_path: Path):
    host = Host(plugin_dir=tmp_path)
    host.enforcer = PermissionEnforcer()
    host.enforcer.register("plug", {"peers": []})

    class Client:
        async def send_message(self, peer, message, **kwargs):
            return SendResult.SUCCESS

    host.a2a_client = Client()
    plugin = SimpleNamespace(name="plug")
    send = host._wrap_send(plugin)

    result = await send("main", "hello")

    assert result == SendResult.SUCCESS
