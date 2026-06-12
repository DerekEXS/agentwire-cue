"""v1.4.1 regression tests for the 6 BUGs found during v1.4 P0 #1 implementation.

These tests prevent the 6 BUGs from regressing:
1. host.py import path (.core.types → .types)
2. Plugin.statechart default None, host must construct
3. 18801 port conflict → 3 retries with 1s backoff
4. admin_api.host.now_ms() doesn't exist (use _now_ms module function)
5. host.plugin_dir must be either file or dir (not require dir)
6. a2a_client send_a2a peer="self" must route to own a2a_url (not fail)
"""
from __future__ import annotations

import asyncio
import socket
import tempfile
import textwrap
from pathlib import Path

import pytest

from agentwire_cue.core.host import Host
from agentwire_cue.core.a2a_client import A2AClient, SendResult
from agentwire_cue.core import host as host_module
from agentwire_cue.core import a2a_client as a2a_module


# ---------- BUG 1: host.py import path ----------
# Spec: must not import from .core.types (which doesn't exist)
# Test: import succeeds and types is accessible from .types

class TestBug1ImportPath:
    def test_host_module_does_not_import_from_core_types(self):
        """v1.4.1 regression: host.py must not have from .core.types import."""
        import inspect
        source = inspect.getsource(host_module)
        # Must not have the wrong import path
        assert "from .core.types import" not in source, (
            "BUG 1 regression: host.py has 'from .core.types import' which is wrong path"
        )

    def test_types_importable_from_core(self):
        from agentwire_cue.core import types
        assert hasattr(types, 'Plugin')


# ---------- BUG 2: Plugin.statechart default None ----------
# Spec: host.start() must construct StatechartEngine before setting _a2a_reply

class TestBug2StatechartConstruction:
    def test_host_constructs_statechart_if_none(self, tmp_path):
        """v1.4.1 regression: host must create statechart for plugins that don't have one."""
        # load_plugin() creates Plugin without statechart (statechart=None)
        from agentwire_cue.core.loader import load_plugin
        yaml_text = textwrap.dedent("""\
            apiVersion: agentwire/v1.2
            kind: plugin
            metadata:
              name: no-sc
              version: 0.1.0
            spec:
              triggers:
                - id: tinc
                  type: a2a_message_type
                  config: { match: "*" }
              statechart:
                initial: idle
                states:
                  idle: { type: final }
              secrets: []
              permissions:
                network: { http_egress: [], raw_socket: false }
                filesystem: []
                subprocess: { allow: [] }
                env: []
                peers: []
                timers: { max_concurrent: 1, min_interval_ms: 1000 }
            """)
        (tmp_path / "p.yaml").write_text(yaml_text)
        plugin = load_plugin(tmp_path / "p.yaml")
        assert plugin is not None
        assert plugin.statechart is None  # loader doesn't construct

        # Verify host.start() creates the statechart
        async def test():
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                a2a_port = s.getsockname()[1]
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                admin_port = s.getsockname()[1]
            host = Host(
                plugin_dir=tmp_path / "p.yaml",
                admin_token="t",
                admin_port=admin_port,
                a2a_listener_port=a2a_port,
            )
            await host.start()
            try:
                # After start, plugin.statechart is NOT None
                p = host.plugins["no-sc"]
                assert p.statechart is not None, "BUG 2 regression: host did not construct statechart"
            finally:
                await host.shutdown()
        asyncio.run(test())


# ---------- BUG 3: 18801 port conflict → 3 retries ----------
# Spec: A2AListener.start() retries 3 times with 1s backoff on OSError
# Test: hold port 18801, start a 2nd listener, expect 3 retries + RuntimeError

class TestBug3PortRetry:
    def test_port_conflict_retries_3_times(self, tmp_path):
        """v1.4.1 regression: A2AListener must retry bind 3 times before failing."""
        from agentwire_cue.core.a2a_client import A2AListener
        # Hold port 18801
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            blocker.bind(('127.0.0.1', 0))
            port = blocker.getsockname()[1]
            blocker.listen(1)

            async def test():
                listener = A2AListener(host='127.0.0.1', port=port)
                import time
                t0 = time.perf_counter()
                try:
                    await listener.start()
                    assert False, "should have failed"
                except RuntimeError as e:
                    elapsed = time.perf_counter() - t0
                    # 3 attempts with ~1s sleep between = ~2s
                    assert elapsed >= 2.0, f"too fast ({elapsed:.1f}s), didn't retry 3x"
                    assert "3 attempts" in str(e)
            asyncio.run(test())
        finally:
            blocker.close()


# ---------- BUG 4: admin_api.host.now_ms() → _now_ms() ----------
# Spec: admin_api must not call host.now_ms() (method doesn't exist)
# Test: admin API responds 200 (which uses _now_ms() now)

class TestBug4NowMsFunction:
    def test_admin_uses_module_function_not_method(self):
        """v1.4.1 regression: admin_api must not call host.now_ms()."""
        import inspect
        from agentwire_cue.core import admin_api
        source = inspect.getsource(admin_api)
        # Must not have host.now_ms() (method that doesn't exist)
        assert "host.now_ms()" not in source, (
            "BUG 4 regression: admin_api calls host.now_ms() (method doesn't exist)"
        )
        # Should import now_ms as a module function
        assert "from .host import now_ms" in source or "_now_ms" in source

    def test_admin_status_returns_200(self, tmp_path):
        """Functional test: admin /status works."""
        yaml_text = textwrap.dedent("""\
            apiVersion: agentwire/v1.2
            kind: plugin
            metadata:
              name: admtst
              version: 0.1.0
            spec:
              triggers:
                - id: tinc
                  type: a2a_message_type
                  config: { match: "*" }
              statechart:
                initial: idle
                states:
                  idle: { type: final }
              secrets: []
              permissions:
                network: { http_egress: [], raw_socket: false }
                filesystem: []
                subprocess: { allow: [] }
                env: []
                peers: []
                timers: { max_concurrent: 1, min_interval_ms: 1000 }
            """)
        (tmp_path / "p.yaml").write_text(yaml_text)

        async def test():
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                a2a_port = s.getsockname()[1]
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                admin_port = s.getsockname()[1]
            host = Host(
                plugin_dir=tmp_path / "p.yaml",
                admin_token="t",
                admin_port=admin_port,
                a2a_listener_port=a2a_port,
            )
            await host.start()
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f'http://127.0.0.1:{admin_port}/status',
                        headers={'Authorization': 'Bearer t'},
                    ) as resp:
                        assert resp.status == 200, f"got {resp.status}"
                        body = await resp.json()
                        assert 'uptime_ms' in body
                        assert isinstance(body['uptime_ms'], int)
                        assert body['uptime_ms'] >= 0
            finally:
                await host.shutdown()
        asyncio.run(test())


# ---------- BUG 5: host.plugin_dir must accept file ----------
# Spec: host should accept either a single file or a directory as plugin_dir

class TestBug5PluginDirFile:
    def test_host_accepts_single_file(self, tmp_path):
        """v1.4.1 regression: Host(plugin_dir=<file>) must work."""
        yaml_text = textwrap.dedent("""\
            apiVersion: agentwire/v1.2
            kind: plugin
            metadata:
              name: tfile
              version: 0.1.0
            spec:
              triggers:
                - id: tinc
                  type: a2a_message_type
                  config: { match: "*" }
              statechart:
                initial: idle
                states:
                  idle: { type: final }
              secrets: []
              permissions:
                network: { http_egress: [], raw_socket: false }
                filesystem: []
                subprocess: { allow: [] }
                env: []
                peers: []
                timers: { max_concurrent: 1, min_interval_ms: 1000 }
            """)
        (tmp_path / "single.yaml").write_text(yaml_text)

        async def test():
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                a2a_port = s.getsockname()[1]
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                admin_port = s.getsockname()[1]
            # Pass FILE not directory
            host = Host(
                plugin_dir=tmp_path / "single.yaml",
                admin_token="t",
                admin_port=admin_port,
                a2a_listener_port=a2a_port,
            )
            await host.start()
            try:
                assert "tfile" in host.plugins
            finally:
                await host.shutdown()
        asyncio.run(test())


# ---------- BUG 6: a2a_client send_a2a peer="self" must route ----------

class TestBug6SendToSelf:
    def test_send_to_self_returns_own_url(self):
        """v1.4.1 regression: _resolve_peer_url('self') must return own a2a_url."""
        import inspect
        client = A2AClient(a2a_url="http://myhost:18800", a2a_token=None)
        url = asyncio.run(client._resolve_peer_url("self"))
        assert url == "http://myhost:18800", f"BUG 6 regression: 'self' returned {url}"
        await_client_close = asyncio.run(client.close())


# ---------- P50/P99 regression: host startup ----------

class TestHostStartupPerformance:
    def test_host_startup_p99_under_500ms(self, tmp_path):
        """v1.4.1 §2.1.1 SLO: host startup P99 <500ms."""
        yaml_text = textwrap.dedent("""\
            apiVersion: agentwire/v1.2
            kind: plugin
            metadata:
              name: perf
              version: 0.1.0
            spec:
              triggers:
                - id: tinc
                  type: a2a_message_type
                  config: { match: "*" }
              statechart:
                initial: idle
                states:
                  idle: { type: final }
              secrets: []
              permissions:
                network: { http_egress: [], raw_socket: false }
                filesystem: []
                subprocess: { allow: [] }
                env: []
                peers: []
                timers: { max_concurrent: 1, min_interval_ms: 1000 }
            """)
        (tmp_path / "p.yaml").write_text(yaml_text)

        import time
        async def test():
            times = []
            for i in range(5):
                with socket.socket() as s:
                    s.bind(('127.0.0.1', 0))
                    a2a_port = s.getsockname()[1]
                with socket.socket() as s:
                    s.bind(('127.0.0.1', 0))
                    admin_port = s.getsockname()[1]
                host = Host(
                    plugin_dir=tmp_path / "p.yaml",
                    admin_token="t",
                    admin_port=admin_port,
                    a2a_listener_port=a2a_port,
                )
                t0 = time.perf_counter()
                await host.start()
                times.append((time.perf_counter() - t0) * 1000)
                await host.shutdown()
            times.sort()
            p99 = times[-1]  # small sample, use max
            assert p99 < 500, f"P99 {p99:.1f}ms > 500ms SLO"
        asyncio.run(test())
