"""AgentWire-Cue v1.5.2 doctor: deployment health checks.

A small surface of stateless functions, each returning a ``DoctorResult``.
The CLI (``agentwire-cue doctor``) composes them and prints one line
per check. Each function is safe to call from tests (no global state,
no sys.exit, no logging side-effects on import).

Categories:
- Token health: file readable, no BOM / CRLF (matches the v1.4.2
  ``utf-8-sig`` fix's expectations).
- CORE reachability: GET ``/.well-known/agent.json``.
- Peer reachability: same probe against each configured peer URL.
- Port conflicts: 18801 (A2A listener) / 19000 (admin).
- Proxy env vars: ``http_proxy``/``https_proxy``/``all_proxy`` set
  while running on a loopback CORE typically funnels traffic through
  the proxy, breaking auth.
- Plugin dependencies: re-runs ``Host._check_requires`` without
  starting the host.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DoctorResult:
    name: str
    status: str  # "ok" | "info" | "warn" | "fail"
    message: str


_PROXY_ENV_VARS = (
    "http_proxy", "https_proxy", "all_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
)


# ---------- token health ----------


def check_token_file(path: Path) -> DoctorResult:
    name = f"Token file {path}"
    if not Path(path).exists():
        return DoctorResult(name, "fail", f"token file does not exist: {path}")
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return DoctorResult(name, "fail", f"cannot read token file: {e}")
    if raw.startswith(b"\xef\xbb\xbf"):
        return DoctorResult(name, "warn",
                            "token file starts with UTF-8 BOM — strip it or use utf-8-sig readers")
    if b"\r\n" in raw or raw.endswith(b"\r"):
        return DoctorResult(name, "warn",
                            "token file contains CRLF line endings — convert to LF")
    return DoctorResult(name, "ok", "token file looks healthy")


# ---------- proxy env ----------


def check_proxy_env() -> DoctorResult:
    name = "Proxy env vars"
    set_vars = [v for v in _PROXY_ENV_VARS if os.environ.get(v)]
    if not set_vars:
        return DoctorResult(name, "ok", "no proxy env vars set")
    return DoctorResult(
        name, "warn",
        f"proxy env vars set ({', '.join(set_vars)}) — may break CUE↔CORE on loopback",
    )


def _port_owner_command(port: int) -> str | None:
    try:
        out = subprocess.check_output(
            ["ss", "-ltnp", f"sport = :{port}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    for line in out.splitlines():
        if "users:((" in line:
            return line
    return None


def check_port_available(port: int, *, host: str = "127.0.0.1") -> DoctorResult:
    """Returns ok if we could bind ``port``, warn if it's busy.

    ``port == 0`` always returns ok (OS-chosen ephemeral, used by tests).
    """
    name = f"Port {port}"
    if port == 0:
        return DoctorResult(name, "ok", "ephemeral port (0) always available")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
    except OSError as e:
        owner = _port_owner_command(port)
        if owner and "agentwire_cue" in owner:
            return DoctorResult(name, "ok", f"port {port} is already owned by agentwire_cue")
        if owner:
            return DoctorResult(name, "warn", f"port {port} in use by another process ({e})")
        return DoctorResult(name, "info", f"port {port} in use; owner unavailable ({e})")
    finally:
        s.close()
    return DoctorResult(name, "ok", f"port {port} available")


# ---------- CORE / peer reachability ----------


async def _probe_url(url: str, timeout_s: float = 2.0) -> tuple[bool, str]:
    """Returns (reachable, detail)."""
    import aiohttp
    if not url:
        return False, "empty url"
    base = url.rstrip("/")
    well_known = f"{base}/.well-known/agent.json"
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(well_known) as resp:
                if 200 <= resp.status < 300:
                    return True, f"HTTP {resp.status}"
                return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_core_reachable(a2a_url: str) -> DoctorResult:
    name = f"CORE ({a2a_url})"
    try:
        reachable, detail = asyncio.run(_probe_url(a2a_url))
    except RuntimeError:
        # Already in an event loop (e.g. invoked from async context).
        loop = asyncio.new_event_loop()
        try:
            reachable, detail = loop.run_until_complete(_probe_url(a2a_url))
        finally:
            loop.close()
    if reachable:
        return DoctorResult(name, "ok", f"reachable ({detail})")
    # v1.5.7: when the operator passes the in-container hostname
    # (e.g. http://agentwire-core:18800) and DNS is not resolvable,
    # the probe is meaningless from the host shell. Downgrade to INFO
    # so the healthcheck and doctor CLI stay clean for valid local
    # network/port wiring.
    if a2a_url and "agentwire-core" in a2a_url and "NameResolutionError" in detail:
        return DoctorResult(name, "info", f"skipped in container DNS context: {detail}")
    if "ConnectionRefused" in detail and a2a_url.startswith("http://127.0.0.1"):
        return DoctorResult(name, "info", f"core not listening on loopback: {detail}")
    return DoctorResult(name, "fail", f"unreachable: {detail}")


def check_peers_reachable(peers: Iterable[tuple[str, str]]) -> list[DoctorResult]:
    """``peers`` is a list of (alias, url) tuples."""
    results: list[DoctorResult] = []
    for alias, url in peers:
        name = f"Peer {alias} ({url})"
        try:
            reachable, detail = asyncio.run(_probe_url(url))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                reachable, detail = loop.run_until_complete(_probe_url(url))
            finally:
                loop.close()
        status = "ok" if reachable else "warn"
        msg = f"reachable ({detail})" if reachable else f"unreachable: {detail}"
        results.append(DoctorResult(name, status, msg))
    return results


# ---------- plugin dependencies ----------


def check_plugin_dependencies(plugin_dir: Path) -> DoctorResult:
    """Run ``Host._check_requires`` without starting the host."""
    from .loader import load_all
    from .host import Host

    name = "Plugin dependencies"
    try:
        plugins = load_all(Path(plugin_dir))
    except Exception as e:
        return DoctorResult(name, "fail", f"could not load plugins: {e}")
    if not plugins:
        return DoctorResult(name, "warn", f"no plugins found under {plugin_dir}")

    host = Host(plugin_dir=Path(plugin_dir))
    host.plugins = {p.name: p for p in plugins}
    host._check_requires()
    degraded = [p for p in plugins if getattr(p, "degraded", False)]
    if not degraded:
        return DoctorResult(name, "ok", f"all {len(plugins)} plugin(s) satisfied")
    parts = [f"{p.name} ({p.degraded_reason})" for p in degraded]
    return DoctorResult(name, "fail", "; ".join(parts))


# ---------- helpers ----------


def collect_peers_from_plugins(plugin_dir: Path) -> list[tuple[str, str]]:
    """Return (alias, url) for every peer declared across plugins."""
    from .loader import load_all
    try:
        plugins = load_all(Path(plugin_dir))
    except Exception:
        return []
    peers: dict[str, str] = {}
    for p in plugins:
        for alias, meta in (getattr(p, "peers", {}) or {}).items():
            url = (meta or {}).get("url")
            if url:
                peers.setdefault(alias, url)
    return sorted(peers.items())
