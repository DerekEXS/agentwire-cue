"""3 new action types from v1.3 H1 fix: write_file, spawn_subprocess, http_request.

These handlers are registered into ACTION_REGISTRY at import time. They all
require a `permission_enforcer` to be injected into the StatechartEngine
(via the `enforcer=` constructor arg). If no enforcer is provided, the
actions fail-fast — they MUST NOT silently bypass the enforcer.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from .loader import get_safe_env
from .statechart import ActionError, EvalEnv, register_action
from ..core.types import Plugin  # noqa: F401  (typing)

log = logging.getLogger("agentwire_cue.actions")

# Module-level enforcer reference, set by the host. If None, the 3 new
# actions fail-fast (per v1.3 H1 fix: they must be guarded).
_ENFORCER: "PermissionEnforcer | None" = None  # type: ignore[name-defined]
_PLUGIN_REGISTRY: dict[str, "Plugin"] = {}  # name -> plugin (for get_safe_env)


def install(enforcer: "PermissionEnforcer", plugins: dict[str, "Plugin"]) -> None:
    """Wire the enforcer + plugin registry. Called once at host startup."""
    global _ENFORCER
    _ENFORCER = enforcer
    _PLUGIN_REGISTRY.clear()
    _PLUGIN_REGISTRY.update(plugins)


def _require_enforcer(action: str):
    if _ENFORCER is None:
        raise ActionError(
            f"{action} requires permission enforcer (host not initialized)"
        )
    return _ENFORCER


# ---------- write_file ----------

async def _action_write_file(action: dict, env: "EvalEnv") -> None:
    enforcer = _require_enforcer("write_file")
    plugin = _PLUGIN_REGISTRY.get(env.plugin_name)
    if plugin is None:
        raise ActionError(f"plugin {env.plugin_name!r} not registered with action installer")
    path_str = action["with"]["path"]
    # v1.6.1: render template variables in path as well
    from .expression import render_template
    if "{{" in str(path_str):
        path_str = render_template(str(path_str), env.as_dict())
    mode = action["with"].get("mode", "write")
    decision = enforcer.check_filesystem(env.plugin_name, path_str, mode)
    if not decision:
        raise ActionError(f"permission denied: {decision.detail}")
    content = action["with"]["content"]
    # Render template if it contains {{...}}
    if "{{" in str(content):
        content = render_template(str(content), env.as_dict())
    p = Path(os.path.expanduser(path_str))
    # M2: parent must exist; refuse to follow symlinks for new file
    p.parent.mkdir(parents=True, exist_ok=True)
    # O_NOFOLLOW semantics: if target exists and is a symlink, refuse
    if p.exists() and p.is_symlink():
        raise ActionError(f"refusing to write to symlink: {p}")
    p.write_text(str(content), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass  # chmod on Windows is best-effort
    log.info("plugin=%s wrote %s (%d bytes)", env.plugin_name, p, len(str(content)))


# ---------- spawn_subprocess ----------

_SAFE_CMD_TOKEN = re.compile(r"^[A-Za-z0-9_./-]+$")


async def _action_spawn_subprocess(action: dict, env: "EvalEnv") -> None:
    enforcer = _require_enforcer("spawn_subprocess")
    cmd = action["with"]["cmd"]
    if not isinstance(cmd, list) or not cmd:
        raise ActionError("spawn_subprocess 'cmd' must be a non-empty list of strings")
    decision = enforcer.check_subprocess(env.plugin_name, cmd)
    if not decision:
        raise ActionError(f"permission denied: {decision.detail}")
    explicit_env = action["with"].get("env", {}) or {}
    if not isinstance(explicit_env, dict):
        raise ActionError("spawn_subprocess 'env' must be a dict")
    for var_name in explicit_env:
        e = enforcer.check_env(env.plugin_name, var_name)
        if not e:
            raise ActionError(f"env var denied: {e.detail}")
    # Build safe env (spec §3.5.1): explicit env, not os.environ full
    plugin = _PLUGIN_REGISTRY.get(env.plugin_name)
    plugin_secrets = plugin.secrets if plugin else {}
    safe_env = {**get_safe_env(plugin_secrets), **explicit_env}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=safe_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise ActionError(f"command not found: {cmd[0]}") from e
    # Wait but don't block transition indefinitely
    try:
        await asyncio.wait_for(proc.wait(), timeout=action["with"].get("timeout", 30))
    except asyncio.TimeoutError:
        proc.kill()
        raise ActionError(f"subprocess timeout: {cmd}")


# ---------- http_request ----------

async def _action_http_request(action: dict, env: "EvalEnv") -> None:
    enforcer = _require_enforcer("http_request")
    url = action["with"]["url"]
    method = action["with"].get("method", "GET").upper()
    # H1 fix: BOTH http_egress AND raw_socket checks
    d_http = enforcer.check_network_http(env.plugin_name, url)
    if not d_http:
        raise ActionError(f"permission denied: {d_http.detail}")
    d_raw = enforcer.check_network_raw(env.plugin_name)
    if not d_raw:
        raise ActionError(f"permission denied: {d_raw.detail}")
    # Render template fields
    from .expression import render_template
    rendered_options: dict = {}
    for k, v in (action["with"].get("options", {}) or {}).items():
        if isinstance(v, str) and "{{" in v:
            rendered_options[k] = render_template(v, env.as_dict())
        else:
            rendered_options[k] = v
    # Lazy import: aiohttp is only needed for http_request action
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=action["with"].get("timeout", 30))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, **rendered_options) as resp:
            await resp.read()  # drain; don't keep the connection open


def install_actions() -> None:
    """Register the 3 H1-fix action handlers. Idempotent."""
    register_action("write_file", _action_write_file)
    register_action("spawn_subprocess", _action_spawn_subprocess)
    register_action("http_request", _action_http_request)
