"""AgentWire-Cue v1.4 §10: Admin API with Bearer token auth (D5a)."""
from __future__ import annotations

import functools
import hmac
import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

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
    event_payload = body.get('payload', {})
    p = host.plugins[name]
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
    from .statechart import Event
    event = Event(
        type=event_type,
        payload=event_payload,
        message_id=body.get('message_id'),
    )
    result = await p.statechart.transition(event)
    return web.json_response({
        'status': 'accepted',
        'new_state': p.statechart.current_state,
        'matched': result.OK,
    })
