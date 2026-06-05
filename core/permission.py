"""AgentWire-Cue v1.3 permission enforcer.

Implements v1.3 §8 spec (P0+P1 fixes from R2/R3 review):
- 5 categories: network (http_egress + raw_socket), filesystem, subprocess, env, peers, timers
- URL scheme whitelist ({http, https} only) — M4
- LD_PRELOAD bypass documentation — M1
- TOCTOU + O_NOFOLLOW defense for filesystem writes — M2
- subprocess allowlist check — M3
- sensitive field filter (shared with persistence §9 + admin §10) — M5
- timers.max_concurrent enforcement

Known gaps (v1.3 §3.5.2, 破晓 netdawn C1): DNS leak, native binary bypass,
/proc/self/environ, signal injection, Python in-process env access.
v1.4 评估 mount namespace / seccomp / Python sandbox.
"""
from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .sandbox import check_filesystem_path

log = logging.getLogger("agentwire_cue.permission")

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
_SENSITIVE_PATTERNS = ("*token*", "*secret*", "*password*", "*credential*")


class PermissionError_(Exception):
    """Raised when an action violates a permission rule."""


@dataclass
class PermissionDecision:
    """Returned to callers; included in trace events."""
    allowed: bool
    category: str
    detail: str
    plugin: str

    def __bool__(self) -> bool:
        return self.allowed


class PermissionEnforcer:
    """One per host. Holds the registered permissions for every plugin.

    Spec §8.3.2.1: http_request MUST check both http_egress AND raw_socket,
    so a raw_socket=false plugin can't bypass network restrictions via HTTP.
    """

    def __init__(self) -> None:
        self._by_plugin: dict[str, dict] = {}
        self._active_timers: dict[str, int] = {}  # plugin -> active count
        self._timer_max: dict[str, int] = {}
        # v1.3.1 L3: persist sandbox per plugin (from spec.persist.allowed_parents_extras)
        self._persist_spec_extras: dict[str, list[str]] = {}
        # v1.3.1 L3: persist sandbox CLI extras (host-level, --persist-allow-parent)
        self._persist_cli_extras: list[str] = []

    # ---------- registration ----------

    def register(self, plugin_name: str, permissions: dict) -> None:
        """Idempotent register; spec §3.6 MUST fail-fast on duplicate."""
        if plugin_name in self._by_plugin:
            raise PermissionError_(f"plugin {plugin_name!r} already registered")
        # Defensive defaults
        perms = {
            "network": {"http_egress": [], "raw_socket": False},
            "filesystem": [],
            "subprocess": {"allow": []},
            "env": [],
            "peers": [],
            "timers": {"max_concurrent": 1, "min_interval_ms": 0},
        }
        if isinstance(permissions, dict):
            if "network" in permissions and isinstance(permissions["network"], dict):
                perms["network"].update(permissions["network"])
            for k in ("filesystem", "subprocess", "env", "peers"):
                if k in permissions:
                    perms[k] = permissions[k]
            if "timers" in permissions and isinstance(permissions["timers"], dict):
                perms["timers"].update(permissions["timers"])
        self._by_plugin[plugin_name] = perms
        self._active_timers[plugin_name] = 0
        self._timer_max[plugin_name] = int(perms["timers"].get("max_concurrent", 1))
        # v1.3.1: capture persist.allowed_parents_extras (used by sandbox at L3)
        persist_cfg = permissions.get("persist", {}) if isinstance(permissions, dict) else {}
        self._persist_spec_extras[plugin_name] = list(persist_cfg.get("allowed_parents_extras", []) or [])

    def unregister(self, plugin_name: str) -> None:
        self._by_plugin.pop(plugin_name, None)
        self._active_timers.pop(plugin_name, None)
        self._timer_max.pop(plugin_name, None)
        self._persist_spec_extras.pop(plugin_name, None)

    def set_cli_persist_extras(self, extras: list[str]) -> None:
        """Host-level: --persist-allow-parent=PATH CLI flag values."""
        self._persist_cli_extras = list(extras)

    def get(self, plugin_name: str) -> dict:
        return self._by_plugin.get(plugin_name, {})

    # ---------- network ----------

    def check_network_http(self, plugin_name: str, url: str) -> PermissionDecision:
        perms = self.get(plugin_name)
        net = perms.get("network", {})
        # M4: URL scheme whitelist
        scheme = urlparse(url).scheme.lower()
        if scheme not in _ALLOWED_URL_SCHEMES:
            return PermissionDecision(
                allowed=False, category="network.http_egress",
                detail=f"URL scheme {scheme!r} not in whitelist (allowed: {sorted(_ALLOWED_URL_SCHEMES)})",
                plugin=plugin_name,
            )
        # Host glob match against http_egress list
        host = (urlparse(url).hostname or "").lower()
        allowed_hosts = net.get("http_egress", []) or []
        if not any(fnmatch.fnmatch(host, pattern.lower()) for pattern in allowed_hosts):
            return PermissionDecision(
                allowed=False, category="network.http_egress",
                detail=f"host {host!r} not in http_egress list {allowed_hosts!r}",
                plugin=plugin_name,
            )
        return PermissionDecision(True, "network.http_egress", f"http ok: {url}", plugin_name)

    def check_network_raw(self, plugin_name: str) -> PermissionDecision:
        perms = self.get(plugin_name)
        if perms.get("network", {}).get("raw_socket", False):
            return PermissionDecision(True, "network.raw_socket", "raw socket allowed", plugin_name)
        return PermissionDecision(
            allowed=False, category="network.raw_socket",
            detail="plugin disallows raw_socket",
            plugin=plugin_name,
        )

    # ---------- filesystem ----------

    def check_filesystem(
        self,
        plugin_name: str,
        path: str,
        mode: str,
    ) -> PermissionDecision:
        if mode not in ("read", "write"):
            return PermissionDecision(
                allowed=False, category="filesystem",
                detail=f"unknown mode {mode!r} (expected 'read' or 'write')",
                plugin=plugin_name,
            )
        perms = self.get(plugin_name).get("filesystem", []) or []
        expanded = os.path.expanduser(path)
        for entry in perms:
            if not isinstance(entry, dict):
                continue
            p = os.path.expanduser(entry.get("path", ""))
            modes = entry.get("modes", [])
            if mode not in modes:
                continue
            # fnmatch on the absolute path
            if fnmatch.fnmatch(expanded, p):
                # v1.3.1 L3: writes must also pass the path sandbox
                if mode == "write":
                    try:
                        check_filesystem_path(
                            path,
                            spec_extras=self._persist_spec_extras.get(plugin_name, []),
                            cli_extras=self._persist_cli_extras,
                        )
                    except Exception as e:
                        return PermissionDecision(
                            allowed=False, category="filesystem",
                            detail=f"path sandbox denied: {e}",
                            plugin=plugin_name,
                        )
                return PermissionDecision(True, "filesystem", f"{mode} ok: {expanded}", plugin_name)
        return PermissionDecision(
            allowed=False, category="filesystem",
            detail=f"{mode} on {path!r} not allowed (rules: {perms!r})",
            plugin=plugin_name,
        )

    # ---------- subprocess ----------

    def check_subprocess(self, plugin_name: str, cmd: list[str]) -> PermissionDecision:
        if not cmd:
            return PermissionDecision(False, "subprocess", "empty cmd", plugin_name)
        binary = os.path.basename(cmd[0])
        allow = self.get(plugin_name).get("subprocess", {}).get("allow", []) or []
        if not any(fnmatch.fnmatch(binary, pattern) for pattern in allow):
            return PermissionDecision(
                allowed=False, category="subprocess",
                detail=f"binary {binary!r} not in subprocess.allow {allow!r}",
                plugin=plugin_name,
            )
        return PermissionDecision(True, "subprocess", f"allow: {binary}", plugin_name)

    # ---------- env ----------

    def check_env(self, plugin_name: str, var_name: str) -> PermissionDecision:
        allow = self.get(plugin_name).get("env", []) or []
        if not any(fnmatch.fnmatch(var_name, p) for p in allow):
            return PermissionDecision(
                allowed=False, category="env",
                detail=f"env var {var_name!r} not in env allow-list {allow!r}",
                plugin=plugin_name,
            )
        return PermissionDecision(True, "env", f"env allow: {var_name}", plugin_name)

    # ---------- peers ----------

    def check_peer(self, plugin_name: str, peer_id: str, message_type: str | None = None) -> PermissionDecision:
        peers = self.get(plugin_name).get("peers", []) or []
        for p in peers:
            if not isinstance(p, dict):
                continue
            if p.get("id") == peer_id:
                if message_type is None:
                    return PermissionDecision(True, "peers", f"peer allow: {peer_id}", plugin_name)
                allow_msgs = p.get("allow_messages") or []
                if "*" in allow_msgs or message_type in allow_msgs:
                    return PermissionDecision(True, "peers", f"peer+msg allow: {peer_id}/{message_type}", plugin_name)
                return PermissionDecision(
                    allowed=False, category="peers",
                    detail=f"peer {peer_id!r} disallows message type {message_type!r}",
                    plugin=plugin_name,
                )
        return PermissionDecision(
            allowed=False, category="peers",
            detail=f"peer {peer_id!r} not in peers list {peers!r}",
            plugin=plugin_name,
        )

    # ---------- timers ----------

    def acquire_timer_slot(self, plugin_name: str) -> bool:
        if self._active_timers[plugin_name] >= self._timer_max[plugin_name]:
            return False
        self._active_timers[plugin_name] += 1
        return True

    def release_timer_slot(self, plugin_name: str) -> None:
        if self._active_timers.get(plugin_name, 0) > 0:
            self._active_timers[plugin_name] -= 1

    # ---------- sensitive filter (shared with persistence §9, admin §10) ----------

    def filter_sensitive(self, data: dict, *, extra_exclude: list[str] | None = None) -> dict:
        """Drop keys matching *token* / *secret* / *password* / *credential* patterns.

        Spec §10.5 (M5): shared helper between persistence and admin API.
        """
        out: dict[str, Any] = {}
        patterns = list(_SENSITIVE_PATTERNS)
        for k, v in (data or {}).items():
            lk = k.lower()
            if any(fnmatch.fnmatch(lk, p.lower()) for p in patterns):
                continue
            if extra_exclude and k in extra_exclude:
                continue
            out[k] = v
        return out
