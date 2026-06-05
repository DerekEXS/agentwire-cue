"""Test suite for the 3 H1-fix action types: write_file, spawn_subprocess, http_request.

These actions require the enforcer to be installed via `actions.install()`.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from agentwire_cue.core import actions
from agentwire_cue.core.actions import install, install_actions
from agentwire_cue.core.permission import PermissionEnforcer
from agentwire_cue.core.statechart import ActionError, Event, StatechartEngine
from agentwire_cue.core.types import Plugin


def _plugin(name: str = "test", perms: dict | None = None) -> Plugin:
    spec = {
        "triggers": [],
        "statechart": {
            "initial": "idle",
            "context": {},
            "states": {
                "idle": {"on": {"GO": {"target": "done", "actions": []}}},
                "done": {"type": "final"},
            },
        },
        "secrets": [],
        "permissions": perms or {
            "network": {"http_egress": [], "raw_socket": False},
            "filesystem": [],
            "subprocess": {"allow": []},
            "env": [],
            "peers": [],
            "timers": {"max_concurrent": 1, "min_interval_ms": 0},
        },
    }
    return Plugin(
        name=name, version="0.1.0", api_version="agentwire/v1.2",
        meta={"name": name, "version": "0.1.0"},
        spec=spec, resolved_persist_path=None, permissions=spec["permissions"],
        secrets={}, triggers=[],
    )


@pytest.fixture
def installed():
    install_actions()
    enforcer = PermissionEnforcer()
    plugin = _plugin("test")
    plugins = {plugin.name: plugin}
    for p in plugins.values():
        enforcer.register(p.name, p.permissions)
    install(enforcer, plugins)
    return enforcer, plugin


# ---------- write_file ----------

class TestWriteFile:
    @pytest.mark.asyncio
    async def test_writes_file_with_permission(self, tmp_path: Path, installed):
        enforcer, plugin = installed
        target = tmp_path / "out.txt"
        enforcer.unregister(plugin.name)
        enforcer.register(plugin.name, {
            "filesystem": [{"path": str(tmp_path) + "/*", "modes": ["write"]}],
            "persist": {"allowed_parents_extras": [str(tmp_path)]},
        })
        eng = StatechartEngine(plugin)
        spec = plugin.spec
        spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "write_file", "with": {
                "path": str(target),
                "content": "hello world",
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.OK
        assert target.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_denied_when_no_filesystem_rule(self, tmp_path: Path, installed):
        enforcer, plugin = installed
        # default perms: no filesystem rules
        eng = StatechartEngine(plugin)
        spec = plugin.spec
        spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "write_file", "with": {
                "path": str(tmp_path / "x"),
                "content": "x",
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        # action denied -> transition fails
        assert r.no_transition
        assert not (tmp_path / "x").exists()

    @pytest.mark.asyncio
    async def test_refuses_symlink_target(self, tmp_path: Path, installed):
        enforcer, plugin = installed
        enforcer.unregister(plugin.name)
        enforcer.register(plugin.name, {
            "filesystem": [{"path": str(tmp_path) + "/*", "modes": ["write"]}],
        })
        real = tmp_path / "real.txt"
        real.write_text("victim", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported")
        eng = StatechartEngine(plugin)
        spec = plugin.spec
        spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "write_file", "with": {
                "path": str(link),
                "content": "evil",
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.no_transition
        assert real.read_text() == "victim"  # not overwritten

    @pytest.mark.asyncio
    async def test_fails_fast_without_enforcer(self, tmp_path: Path):
        # No install() call: enforcer is None
        install_actions()
        plugin = _plugin("lone")
        eng = StatechartEngine(plugin)
        plugin.spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "write_file", "with": {
                "path": str(tmp_path / "x"),
                "content": "x",
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.no_transition


# ---------- spawn_subprocess ----------

class TestSpawnSubprocess:
    @pytest.mark.asyncio
    async def test_runs_allowed_binary(self, installed, tmp_path: Path):
        import sys as _sys
        import os
        python_bin = os.path.basename(_sys.executable)
        enforcer, plugin = installed
        enforcer.unregister(plugin.name)
        enforcer.register(plugin.name, {
            "subprocess": {"allow": [python_bin]},
            "env": ["AGENTWIRE_CUE_TEST"],
        })
        eng = StatechartEngine(plugin)
        plugin.spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "spawn_subprocess", "with": {
                "cmd": [_sys.executable, "-c", "import os; os.environ.get('AGENTWIRE_CUE_TEST')"],
                "env": {"AGENTWIRE_CUE_TEST": "injected"},
                "timeout": 5,
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.OK

    @pytest.mark.asyncio
    async def test_denied_binary(self, installed):
        enforcer, plugin = installed
        # default perms: empty allow list
        eng = StatechartEngine(plugin)
        plugin.spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "spawn_subprocess", "with": {
                "cmd": ["rm", "-rf", "/"],
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.no_transition

    @pytest.mark.asyncio
    async def test_env_var_denied(self, installed):
        enforcer, plugin = installed
        enforcer.unregister(plugin.name)
        enforcer.register(plugin.name, {
            "subprocess": {"allow": ["echo"]},
            # env: empty allow list — every env var denied
        })
        eng = StatechartEngine(plugin)
        plugin.spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "spawn_subprocess", "with": {
                "cmd": ["echo", "hi"],
                "env": {"MY_SECRET": "value"},
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.no_transition

    @pytest.mark.asyncio
    async def test_subprocess_runs_in_isolated_env(self, installed, monkeypatch):
        # Set a "host" env var; the subprocess must NOT see it (spec §3.5.1)
        monkeypatch.setenv("HOST_SECRET_LEAK_TEST", "should-not-leak")
        import sys as _sys
        import os
        python_bin = os.path.basename(_sys.executable)
        enforcer, plugin = installed
        enforcer.unregister(plugin.name)
        enforcer.register(plugin.name, {
            "subprocess": {"allow": [python_bin]},
            "env": ["SAFE_VAR"],
        })
        result_file = installed[0]  # not used, just for type
        eng = StatechartEngine(plugin)
        # The subprocess writes 0 or 1 to a temp file
        out = Path("/tmp") / "agentwire-cue-isolation-test.txt"
        if out.exists():
            out.unlink()
        plugin.spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "spawn_subprocess", "with": {
                "cmd": [_sys.executable, "-c",
                        f"open({str(out)!r}, 'w').write('1' if 'HOST_SECRET_LEAK_TEST' in __import__('os').environ else '0')"],
                "env": {"SAFE_VAR": "ok"},
                "timeout": 5,
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.OK
        if out.exists():
            assert out.read_text() == "0", "host env leaked into subprocess!"


# ---------- http_request ----------

class TestHttpRequest:
    @pytest.mark.asyncio
    async def test_denied_url_not_in_egress(self, installed):
        enforcer, plugin = installed
        eng = StatechartEngine(plugin)
        plugin.spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "http_request", "with": {
                "url": "https://other.example.com/api",
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.no_transition

    @pytest.mark.asyncio
    async def test_denied_file_scheme(self, installed):
        enforcer, plugin = installed
        enforcer.unregister(plugin.name)
        enforcer.register(plugin.name, {"network": {"http_egress": ["*"], "raw_socket": False}})
        eng = StatechartEngine(plugin)
        plugin.spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "http_request", "with": {
                "url": "file:///etc/passwd",
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.no_transition

    @pytest.mark.asyncio
    async def test_denied_when_raw_socket_false(self, installed):
        # H1 fix: http_request must check BOTH http_egress AND raw_socket
        enforcer, plugin = installed
        enforcer.unregister(plugin.name)
        enforcer.register(plugin.name, {
            "network": {"http_egress": ["*"], "raw_socket": False}
        })
        eng = StatechartEngine(plugin)
        plugin.spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "http_request", "with": {
                "url": "https://example.com",
            }}
        ]
        r = await eng.transition(Event(type="GO"))
        assert r.no_transition
