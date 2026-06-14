"""AgentWire-Cue v1.3.1 patch 2 commit 3 + v1.4 P0 #1: A2A client + peer card cache.

Implements:
- v1.3.1 patch 2 D4: peer card lazy fetch + 10 min TTL + 持久化 to peers/ sandbox
- v1.4 §7: a2a_client.send_message (HTTP + retry+backoff + caller 编排)
- v1.4 §7.7: 18801 listener (inbound A2A message 路由)
- v1.4 §7.5: fallback dispatcher (caller 编排 D1)

SendResult 4 枚举 (D1):
- SUCCESS: HTTP 200
- FAILED: 4xx/5xx/网络错误, 走 retry
- EXHAUSTED: retry 耗尽, caller 走 fallback
- PERMISSION_DENIED: §8 拒绝, 不进 retry 不进 fallback
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

from .sandbox import (
    SURFACE_PEER,
    SandboxError,
    check_surface_path,
)

log = logging.getLogger("agentwire_cue.a2a_client")
CUE_VERSION = "1.6.2"


def now_ms() -> int:
    return int(time.time() * 1000)


class SendResult(Enum):
    SUCCESS = "success"
    FAILED = "failed"           # 单次失败, 走 retry
    EXHAUSTED = "exhausted"     # retry 耗尽, caller 走 fallback dispatcher
    PERMISSION_DENIED = "permission_denied"  # §8 拒绝, 不重试


# ---------- Peer Card Cache (D4) ----------

class PeerCardCache:
    """v1.3.1 patch 2 D4: lazy fetch + 10 min TTL + 持久化到 peers/.

    Cache file: ~/.local/share/agentwire-cue/peers/<peer_id>.json
    - lazy fetch: 首次 get 才 HTTP 拉
    - TTL: 10 min (600_000 ms) 默认, 可改
    - 写失败降级 memory-only, 不阻塞 send_a2a
    - 沙箱: surface='peer' 强约束
    """

    def __init__(self, cache_dir: Path, ttl_ms: int = 600_000):
        self.cache_dir = cache_dir
        self.ttl_ms = ttl_ms
        # In-memory fallback if disk write fails
        self._memory: dict[str, dict] = {}

    def _cache_path(self, peer_id: str) -> Path:
        return self.cache_dir / f"{peer_id}.json"

    async def get(self, peer_id: str, peer_url: str) -> dict | None:
        """Return peer agent card. None if unreachable.

        Order: memory → disk cache (if fresh) → fetch → write back.
        """
        # 1. memory (always consulted, fastest)
        mem = self._memory.get(peer_id)
        if mem and now_ms() - mem['fetched_at_ms'] < mem['ttl_ms']:
            return mem['card']

        # 2. disk cache
        cache_path = self._cache_path(peer_id)
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text())
                if now_ms() - data.get('fetched_at_ms', 0) < data.get('ttl_ms', 0):
                    self._memory[peer_id] = data
                    return data['card']
            except (json.JSONDecodeError, KeyError, OSError) as e:
                log.warning("peer card cache read failed for %s: %s", peer_id, e)

        # 3. fetch
        card = await self._fetch(peer_id, peer_url)
        if card is None:
            return None

        # 4. write back (sandbox + write-fail-graceful)
        await self._write_back(peer_id, card)
        return card

    async def _fetch(self, peer_id: str, peer_url: str) -> dict | None:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{peer_url.rstrip('/')}/.well-known/agent.json") as resp:
                    if resp.status == 200:
                        return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("peer card fetch failed for %s: %s", peer_id, e)
        return None

    async def _write_back(self, peer_id: str, card: dict) -> None:
        data = {
            'peer_id': peer_id,
            'fetched_at_ms': now_ms(),
            'ttl_ms': self.ttl_ms,
            'card': card,
        }
        # Always update memory (always works)
        self._memory[peer_id] = data
        # Try disk write (sandbox + graceful fail)
        cache_path = self._cache_path(peer_id)
        try:
            check_surface_path(
                str(cache_path), surface=SURFACE_PEER, peer_id=peer_id,
            )
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            try:
                os.chmod(cache_path, 0o600)
            except OSError:
                pass
        except (SandboxError, OSError) as e:
            log.warning(
                "peer card cache write failed for %s (memory-only): %s", peer_id, e
            )

    async def invalidate(self, peer_id: str) -> None:
        """Manual cache invalidation (admin API can call this)."""
        self._memory.pop(peer_id, None)
        cache_path = self._cache_path(peer_id)
        if cache_path.exists():
            try:
                cache_path.unlink()
            except OSError:
                pass


# ---------- Retry Policy ----------

class RetryPolicy:
    """指数 backoff, 上限 30s."""

    def __init__(self, max_retries: int = 2, backoff_ms: int = 500):
        self.max_retries = max_retries
        self.backoff_ms = backoff_ms

    async def execute_with_retry(self, send_fn: Callable[[], Awaitable[SendResult]]) -> SendResult:
        if self.max_retries == 0:
            return await send_fn()
        for attempt in range(self.max_retries + 1):
            result = await send_fn()
            if result == SendResult.SUCCESS:
                return SendResult.SUCCESS
            if result in (SendResult.PERMISSION_DENIED,):
                # Don't retry on permission denied
                return result
            if attempt < self.max_retries:
                delay_ms = min(self.backoff_ms * (2 ** attempt), 30_000)
                await asyncio.sleep(delay_ms / 1000)
        return SendResult.EXHAUSTED


# ---------- A2A Client (HTTP send) ----------

class A2AClient:
    """v1.4 §7: HTTP client + retry + peer card discovery.

    send_message returns SendResult (caller 编排 fallback, D1).
    """

    def __init__(
        self,
        a2a_url: str,
        a2a_token: str | None = None,
        peer_cache: PeerCardCache | None = None,
        retry_policy: RetryPolicy | None = None,
    ):
        self.a2a_url = a2a_url.rstrip('/')
        self.a2a_token = a2a_token
        self.peer_cache = peer_cache
        self.retry_policy = retry_policy or RetryPolicy()
        self._session: aiohttp.ClientSession | None = None
        # v1.4.8: peer alias table. When non-empty, the alias URL is used
        # directly for routing and the peer card cache is bypassed.
        self._aliases: dict[str, dict] = {}

    def set_aliases(self, aliases: dict[str, dict]) -> None:
        """v1.4.8: install the peer alias table for direct URL routing."""
        self._aliases = dict(aliases)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            headers = {'Content-Type': 'application/json'}
            if self.a2a_token:
                headers['Authorization'] = f'Bearer {self.a2a_token}'
            self._session = aiohttp.ClientSession(
                base_url=self.a2a_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _resolve_peer_url(self, peer_id: str) -> str | None:
        """Use peer card cache to find peer's a2a URL. None if not discoverable.

        v1.4.8: when an alias table is configured and `peer_id` matches an
        alias, the configured url is used directly (no peer card fetch).

        Special case: peer_id == "self" returns the OWN a2a_url (for testing
        loopback to the same host's 18801 listener). The A2A listener on this
        host will then route the inbound message to the right plugin.
        """
        if peer_id == "self":
            return self.a2a_url
        alias = self._aliases.get(peer_id)
        if alias is not None:
            return alias["url"]
        if self.peer_cache is None:
            return None
        card = await self.peer_cache.get(peer_id, peer_url=self.a2a_url)
        if card is None:
            return None
        inbound = card.get('endpoints', {}).get('inbound')
        if not inbound:
            return None
        return inbound.rsplit('/a2a/inbound', 1)[0] if '/a2a/inbound' in inbound else inbound

    def resolve_peer_token(self, peer_id: str) -> str:
        """v1.6.1: resolve per-peer A2A token from alias metadata.

        Priority: token_file > token_env > token (literal) > default self.a2a_token.
        When the peer alias is missing, returns self.a2a_token unchanged.
        token_file / token_env are read per-call (not cached at startup)
        so rotating credentials takes effect on the next call.
        """
        alias = self._aliases.get(peer_id)
        if alias is None:
            return self.a2a_token or ''
        token_file = alias.get('token_file')
        if token_file:
            try:
                with open(token_file, 'r') as fh:
                    val = fh.read().strip()
                if val:
                    return val
            except (OSError, PermissionError):
                pass
        token_env = alias.get('token_env')
        if token_env:
            val = os.environ.get(token_env)
            if val:
                return val
        token_literal = alias.get('token')
        if token_literal:
            return token_literal
        return self.a2a_token or ''

    async def send_message(
        self,
        target_peer: str,
        message: dict,
        *,
        permission_check: Callable[[], bool] | None = None,
        metadata: dict | None = None,
    ) -> SendResult:
        """D1: caller 编排. 返 SendResult, 不内置 fallback.

        permission_check: optional callable to check §8 peer allow-list.
        If provided and returns False, returns PERMISSION_DENIED immediately.

        v1.4.8: `metadata` is attached to the outbound `message.metadata`
        field. It is forwarded to CORE only when not None.
        """
        # Permission check (caller-supplied, not coupled to enforcer)
        if permission_check is not None and not permission_check():
            return SendResult.PERMISSION_DENIED

        # Discover peer's URL
        peer_url = await self._resolve_peer_url(target_peer)
        if peer_url is None:
            return SendResult.FAILED

        # v1.5.0: normalize the small CUE action shorthand {text: "..."}
        # into an A2A-compatible message so CORE records non-empty parts.
        outbound_message = dict(message)
        if "parts" not in outbound_message and isinstance(outbound_message.get("text"), str):
            outbound_message = {
                "role": outbound_message.get("role", "user"),
                "parts": [{"type": "text", "text": outbound_message["text"]}],
            }
        if metadata is not None:
            outbound_message["metadata"] = metadata

        # v1.6.1: resolve per-peer token (falls back to default if not configured)
        peer_token = self.resolve_peer_token(target_peer)

        # Retry loop
        async def _do_send() -> SendResult:
            try:
                session = await self._ensure_session()
                # Strip /a2a/jsonrpc suffix if peer_url already has it
                base = peer_url.rstrip('/')
                if base.endswith('/a2a/jsonrpc'):
                    url = base
                else:
                    url = f"{base}/a2a/jsonrpc"
                payload = {
                    'jsonrpc': '2.0',
                    'id': f"cue-{now_ms()}",
                    'method': 'message/send',
                    'params': {'message': outbound_message},
                }
                # v1.6.1: use per-peer token if available
                headers = {}
                if peer_token:
                    headers['Authorization'] = f'Bearer {peer_token}'
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        return SendResult.SUCCESS
                    log.warning("a2a send to %s HTTP %d", target_peer, resp.status)
                    return SendResult.FAILED
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("a2a send to %s network error: %s", target_peer, e)
                return SendResult.FAILED

        return await self.retry_policy.execute_with_retry(_do_send)


# ---------- A2A Listener (inbound 18801) ----------

class A2AListener:
    """v1.4 §7.7: 单中央 18801 listener (D3)."""

    def __init__(self, host: str = '127.0.0.1', port: int = 18801, auth_token: str | None = None):
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.reject_new = False
        self._inbound_handlers: list[Callable[[dict], Awaitable[None]]] = []
        self._runner = None
        self._site = None
        self._plugins_info: list[dict] = []  # for agent card
        # v1.5.6: when bound to a non-loopback interface with no auth token,
        # inbound A2A traffic must be refused outright. Local development
        # on 127.0.0.1 / ::1 / localhost stays permissive.
        self.allow_inbound_without_token = host in ('127.0.0.1', '::1', 'localhost') or host.startswith('127.')

    def register_handler(self, handler: Callable[[dict], Awaitable[None]]) -> None:
        self._inbound_handlers.append(handler)

    def set_plugins_info(self, plugins: list[dict]) -> None:
        self._plugins_info = plugins

    async def start(self) -> None:
        from aiohttp import web
        app = web.Application()
        app.router.add_post('/a2a/inbound', self._handle_inbound)
        app.router.add_get('/.well-known/agent.json', self._handle_agent_card)
        self._runner = web.AppRunner(app)
        await self._runner.setup()

        # v1.3 §2.6: port in use → retry 3 times (1s interval) → fail-fast
        last_error = None
        for attempt in range(3):
            self._site = web.TCPSite(self._runner, self.host, self.port)
            try:
                await self._site.start()
                log.info("A2A listener started on %s:%d (attempt %d)",
                         self.host, self.port, attempt + 1)
                return
            except OSError as e:
                last_error = e
                log.warning("A2A listener bind %s:%d failed (attempt %d): %s",
                            self.host, self.port, attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep(1)
        raise RuntimeError(
            f"A2A listener failed to bind {self.host}:{self.port} after 3 attempts: {last_error}"
        )

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()
        log.info("A2A listener stopped")

    async def _handle_inbound(self, request):
        from aiohttp import web
        if self.reject_new:
            return web.json_response(
                {'error': 'draining'}, status=503,
            )
        if self.auth_token:
            auth = request.headers.get('Authorization', '')
            if not auth.startswith('Bearer ') or not hmac.compare_digest(auth[7:], self.auth_token):
                return web.json_response({'error': 'unauthorized'}, status=401)
        else:
            if not self.allow_inbound_without_token:
                from aiohttp import web
                log.warning("A2A inbound refused: bound %s without token", self.host)
                return web.json_response(
                    {'error': 'bound_without_token', 'detail': f"listener binds {self.host} but no auth_token is configured"},
                    status=403,
                )
            log.warning("A2A inbound auth disabled because no token is configured")
        body = await request.json()
        message = body.get('params', {}).get('message', {})
        for handler in self._inbound_handlers:
            try:
                if handler.__self__.matches(message) if hasattr(handler, '__self__') else True:
                    await handler(message)
                    return web.json_response({
                        'jsonrpc': '2.0',
                        'id': body.get('id'),
                        'result': {'kind': 'task', 'id': 'cue-task', 'status': {'state': 'accepted'}},
                    })
            except Exception as e:
                log.exception("inbound handler error: %s", e)
        # No match — S7.1 fix: 200 + error object (跟 JSON-RPC 协议)
        return web.json_response({
            'jsonrpc': '2.0',
            'id': body.get('id'),
            'error': {'code': -32603, 'message': 'no plugin matches'},
        }, status=200)

    async def _handle_agent_card(self, request):
        from aiohttp import web
        return web.json_response({
            'protocolVersion': '1.0.1',
            'name': 'agentwire-cue',
            'description': f'Host with {len(self._plugins_info)} plugins',
            'version': CUE_VERSION,
            'capabilities': {
                'streaming': False,
                'pushNotifications': False,
                'extendedAgentCard': False,
            },
            'skills': [
                {'id': p['name'], 'name': p['name'], 'description': f"Plugin {p['name']}"}
                for p in self._plugins_info
            ],
            'defaultInputModes': ['text'],
            'defaultOutputModes': ['text'],
            'endpoints': {
                'inbound': f'http://{self.host}:{self.port}/a2a/inbound',
            },
        })


# ---------- Fallback Dispatcher (D1 caller 编排) ----------

class FallbackDispatcher:
    """v1.4 §7.5: caller 编排 fallback (D1).

    Statechart 调 send_a2a 失败 EXHAUSTED 后, 调本类 dispatch(plugin, event).
    """

    def __init__(self, a2a_client: A2AClient):
        self.a2a_client = a2a_client

    async def dispatch(self, plugin, event) -> None:
        """v1.2 spec §6.1 5 步流程 (caller 编排版本)."""
        from .expression import evaluate, parse as parse_expr
        from .statechart import EvalEnv

        # 4. 扫 fallbacks[], 匹配 a2a_exhausted == true 的第一个
        env = EvalEnv(
            plugin, event,
            plugin.statechart.current_state,
            plugin.statechart.state_entered_at_ms,
            plugin.statechart.context,
        )
        for fb in plugin.spec.get('fallbacks', []) or []:
            condition = fb.get('condition', '')
            try:
                if not evaluate(parse_expr(condition), env.as_dict()):
                    continue
            except Exception:
                continue
            await self._run_fallback_actions(fb.get('actions', []), env, plugin)
            return

        # 5. 没匹配 → 转 resilience.on_exhaust 目标 state
        on_exhaust = plugin.spec.get('resilience', {}).get('on_exhaust')
        if on_exhaust:
            await plugin.statechart.transition(
                __import__('agentwire_cue.core.statechart', fromlist=['Event']).Event(
                    type='ON_EXHAUST_FALLBACK',
                    payload={},
                    message_id=None,
                )
            )

    async def _run_fallback_actions(self, actions, env, plugin) -> None:
        """fallback 内 send_a2a 设 from_fallback=True (防递归, spec §6.1.2)."""
        prev = plugin.statechart.from_fallback
        plugin.statechart.from_fallback = True
        try:
            for action in actions:
                await plugin.statechart._dispatch_action(action, env)
        finally:
            plugin.statechart.from_fallback = prev
