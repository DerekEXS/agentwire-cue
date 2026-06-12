"""AgentWire-Cue v1.4 §10: Admin API with Bearer token auth (D5a)."""
from __future__ import annotations

import functools
import hmac
import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from . import observability
from .host import now_ms as _now_ms

if TYPE_CHECKING:
    from .host import Host

log = logging.getLogger("agentwire_cue.admin_api")


def require_admin_token(handler):
    """Decorator: 401 if Authorization header missing/wrong.

    v1.4 §10.2.1 D5a: 用 hmac.compare_digest 防 timing attack.
    """
    @functools.wraps(handler)
    async def wrapper(request: web.Request) -> web.Response:
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return web.json_response({
                'error': 'unauthorized',
                'message': 'Bearer token required',
            }, status=401)
        token = auth[len('Bearer '):]
        expected = request.app['admin_token']
        if not expected:
            return web.json_response({
                'error': 'forbidden',
                'message': 'admin API not configured (no admin_token)',
            }, status=403)
        if not hmac.compare_digest(token, expected):
            return web.json_response({
                'error': 'forbidden',
                'message': 'invalid admin token',
            }, status=403)
        return await handler(request)
    return wrapper


def create_admin_app(host: 'Host') -> web.Application:
    """Build aiohttp app for admin API (port 19000)."""
    app = web.Application()
    app['admin_token'] = host.admin_token
    app['host'] = host
    app.router.add_get('/status', handle_status)
    app.router.add_get('/plugins', handle_plugins)
    app.router.add_get('/plugins/{name}', handle_plugin_detail)
    app.router.add_post('/plugins/{name}/trigger', handle_trigger)
    # v1.5.1: diagnostics endpoints
    app.router.add_get('/admin/status', handle_admin_status)
    app.router.add_get('/admin/peers', handle_admin_peers)
    app.router.add_get('/admin/plugins', handle_admin_plugins)
    return app


@require_admin_token
async def handle_status(request: web.Request) -> web.Response:
    host = request.app['host']
    return web.json_response({
        'status': 'healthy' if not host.draining else 'draining',
        'uptime_ms': _now_ms() - host.started_at_ms,
        'plugin_count': len(host.plugins),
        'plugins': [
            {
                'name': p.name,
                'version': p.version,
                'state_id': p.statechart.current_state,
            }
            for p in host.plugins.values()
        ],
        'a2a_url': host.a2a_url,
        'a2a_listener_port': host.a2a_listener_port,
    })


@require_admin_token
async def handle_plugins(request: web.Request) -> web.Response:
    host = request.app['host']
    return web.json_response({
        'plugins': [
            {
                'name': p.name,
                'version': p.version,
                'api_version': p.api_version,
                'state_id': p.statechart.current_state,
                'context_keys': list(p.statechart.context.keys()),
                'context_size_bytes': len(json.dumps(p.statechart.context).encode()),
                'persist_path': str(p.resolved_persist_path) if p.resolved_persist_path else None,
                'triggers': [t.id for t in p.triggers],
            }
            for p in host.plugins.values()
        ]
    })


@require_admin_token
async def handle_plugin_detail(request: web.Request) -> web.Response:
    host = request.app['host']
    name = request.match_info['name']
    if name not in host.plugins:
        return web.json_response({
            'error': 'plugin_not_found',
            'message': f'plugin {name!r} not loaded',
        }, status=404)
    p = host.plugins[name]
    return web.json_response({
        'name': p.name,
        'version': p.version,
        'api_version': p.api_version,
        'statechart': {
            'initial': p.spec.get('statechart', {}).get('initial'),
            'current_state': p.statechart.current_state,
            'states': list(p.spec.get('statechart', {}).get('states', {}).keys()),
        },
        'context': host.enforcer.filter_sensitive(p.statechart.context) if host.enforcer else p.statechart.context,
        'persist': p.resolved_persist_path is not None,
        'triggers': [{'id': t.id, 'type': t.type} for t in p.triggers],
    })


@require_admin_token
async def handle_trigger(request: web.Request) -> web.Response:
    host = request.app['host']
    name = request.match_info['name']
    if name not in host.plugins:
        return web.json_response({
            'error': 'plugin_not_found',
            'message': f'plugin {name!r} not loaded',
        }, status=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({
            'error': 'bad_request',
            'message': 'invalid JSON body',
        }, status=400)
    event_type = body.get('type')
    if not event_type:
        return web.json_response({
            'error': 'bad_request',
            'message': 'type is required',
        }, status=400)
    event_payload = dict(body.get('payload', {}) or {})
    p = host.plugins[name]
    if event_type == 'history_change' and 'peer' not in event_payload:
        for trigger in getattr(p, 'triggers', []) or []:
            if getattr(trigger, 'type', None) == 'history_change':
                peer = (getattr(trigger, 'config', {}) or {}).get('peer')
                if peer and peer != '*':
                    event_payload['peer'] = peer
                    break
    # v1.3 §10.6 H3 fix: type must in statechart.on{} keys
    allowed = set()
    for state_def in p.spec.get('statechart', {}).get('states', {}).values():
        if isinstance(state_def, dict):
            allowed.update(state_def.get('on', {}).keys())
    if event_type not in allowed:
        return web.json_response({
            'error': 'unknown_event_type',
            'message': f'event_type {event_type!r} not in plugin statechart.on{{}} keys',
            'allowed': sorted(allowed),
        }, status=400)
    # v1.5.1: structured observability — issue a new trace_id per admin
    # trigger call so logs from statechart/action paths share the same id.
    trace_id = observability.new_trace_id()
    observability.set_trace_id(trace_id)
    try:
        observability.emit(
            'cue.trigger.received',
            plugin=name,
            event_type=event_type,
            peer=event_payload.get('peer'),
            source='admin_api',
        )
        from .statechart import Event
        event = Event(
            type=event_type,
            payload=event_payload,
            message_id=body.get('message_id'),
        )
        result = await p.statechart.transition(event)
        response = {
            'status': 'accepted',
            'new_state': p.statechart.current_state,
            'matched': result.OK,
            'trace_id': trace_id,
        }
        if not result.OK:
            response['reason'] = result.reason or ('error' if result.error else 'no_transition')
            response['details'] = result.details or ({'error': result.error} if result.error else {})
            log.info(
                "admin trigger plugin=%s event=%s matched=false reason=%s details=%s",
                name, event_type, response['reason'], response['details'],
            )
        observability.emit(
            'cue.trigger.evaluated',
            plugin=name,
            event_type=event_type,
            matched=result.OK,
            reason=response.get('reason'),
            new_state=p.statechart.current_state,
        )
        # v1.5.1 P2: runtime tracking so /admin/status can answer
        # "last time owner-alert fired, did it match?".
        try:
            p.last_trigger_at = _now_ms()
            p.last_match = result.OK
            p.last_reason = response.get('reason')
            p.last_details = response.get('details')
        except Exception:
            pass
        return web.json_response(response)
    finally:
        observability.reset_trace_id()


# ---------- v1.5.1 admin diagnostics ----------


async def _probe_peer_reachable(url: str, timeout_s: float = 1.0) -> bool:
    """Best-effort peer health probe used by /admin/peers.

    GETs ``/.well-known/agent.json`` with a short timeout. Any HTTP 2xx
    counts as reachable; everything else (timeouts, refused, non-2xx) is
    unreachable. Failures never raise to the caller.
    """
    import aiohttp
    if not url:
        return False
    base = url.rstrip('/')
    well_known = f"{base}/.well-known/agent.json"
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(well_known) as resp:
                return 200 <= resp.status < 300
    except Exception:
        return False


@require_admin_token
async def handle_admin_status(request: web.Request) -> web.Response:
    """v1.5.1 §10: per-plugin runtime state with last-trigger bookkeeping."""
    host = request.app['host']
    from .a2a_client import CUE_VERSION
    uptime_seconds = max(0, (_now_ms() - host.started_at_ms) // 1000)
    plugins = {}
    for name, p in host.plugins.items():
        last_trigger_at = getattr(p, 'last_trigger_at', None)
        last_match = getattr(p, 'last_match', None)
        last_reason = getattr(p, 'last_reason', None)
        if last_trigger_at is None and last_match is None:
            last_reason = last_reason or 'never_triggered'
        state = getattr(getattr(p, 'statechart', None), 'current_state', None)
        plugins[name] = {
            'state': state,
            'last_trigger_at': last_trigger_at,
            'last_match': last_match,
            'last_reason': last_reason,
            'last_details': getattr(p, 'last_details', None),
        }
    return web.json_response({
        'cue_version': CUE_VERSION,
        'uptime_seconds': uptime_seconds,
        'plugins': plugins,
    })


@require_admin_token
async def handle_admin_peers(request: web.Request) -> web.Response:
    """v1.5.1 §10: peer alias table + best-effort reachable status."""
    host = request.app['host']
    aliases = {}
    client = getattr(host, 'a2a_client', None)
    if client is not None and getattr(client, 'aliases', None):
        aliases = dict(client.aliases)
    else:
        # Aggregate from plugins as a fallback (mirrors host startup logic).
        for p in host.plugins.values():
            for alias, meta in (getattr(p, 'peers', {}) or {}).items():
                aliases[alias] = meta

    peers = {}
    for alias, meta in aliases.items():
        meta = meta or {}
        url = meta.get('url')
        reachable = await _probe_peer_reachable(url) if url else False
        peers[alias] = {
            'uuid': meta.get('uuid'),
            'url': url,
            'reachable': reachable,
        }
    return web.json_response({'peers': peers})


@require_admin_token
async def handle_admin_plugins(request: web.Request) -> web.Response:
    """v1.5.1 §10: minimal loaded plugin roster."""
    host = request.app['host']
    names = sorted(host.plugins.keys())
    return web.json_response({
        'plugins': names,
        'count': len(names),
    })
