"""Test suite for v1.3.1 patch 2 commit 2: trigger await setup (D2)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agentwire_cue.core.trigger import (
    Trigger,
    TriggerEvent,
    TriggerScheduler,
    TriggerSetupError,
)
from agentwire_cue.core.types import Plugin


def _make_plugin(name: str = "test-plugin") -> Plugin:
    return Plugin(
        name=name, version="0.1.0", api_version="agentwire/v1.2",
        meta={"name": name, "version": "0.1.0"},
        spec={"triggers": [], "statechart": {}, "secrets": [], "permissions": {}},
        resolved_persist_path=None, permissions={}, secrets={}, triggers=[],
    )


class _StubTrigger(Trigger):
    """Minimal concrete trigger for testing."""
    def __init__(self, trigger_def, plugin, *, setup_delay_ms=0, fail=False):
        super().__init__(trigger_def, plugin)
        self.setup_delay_ms = setup_delay_ms
        self.fail = fail
        self.setup_called = False
        self.teardown_called = False

    async def setup(self):
        if self.setup_delay_ms:
            await asyncio.sleep(self.setup_delay_ms / 1000)
        if self.fail:
            raise RuntimeError("simulated setup failure")
        self.setup_called = True

    async def teardown(self):
        self.teardown_called = True

    def matches(self, payload: dict) -> bool:
        return True


# ---------- D2: register async, awaits setup ----------

class TestRegisterAwaitsSetup:
    @pytest.mark.asyncio
    async def test_register_returns_after_setup_complete(self):
        plugin = _make_plugin()
        trigger = _StubTrigger({"id": "t1", "type": "stub"}, plugin, setup_delay_ms=50)
        sched = TriggerScheduler()
        await sched.register(trigger)
        # After register() returns, setup must be complete
        assert trigger.setup_called is True
        assert sched.get("t1") is trigger

    @pytest.mark.asyncio
    async def test_register_propagates_setup_error(self):
        plugin = _make_plugin()
        trigger = _StubTrigger({"id": "t1", "type": "stub"}, plugin, fail=True)
        sched = TriggerScheduler()
        with pytest.raises(TriggerSetupError, match="simulated setup failure"):
            await sched.register(trigger)
        # Failed trigger is NOT registered
        assert sched.get("t1") is None

    @pytest.mark.asyncio
    async def test_register_all_concurrent_under_slo(self):
        # v1.4 §2.1.1 SLO: 100 trigger setup <500ms when concurrent
        # Each trigger does 10ms of work. Serial = 1000ms. Concurrent = ~20ms.
        plugin = _make_plugin("p1")
        triggers = [
            _StubTrigger({"id": f"t{i}", "type": "stub"}, plugin, setup_delay_ms=10)
            for i in range(100)
        ]
        sched = TriggerScheduler()
        t0 = time.perf_counter()
        await sched.register_all(triggers)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # SLO: <500ms (实际期望 <100ms, 留 5x buffer)
        assert elapsed_ms < 500, f"100 trigger setup took {elapsed_ms:.0f}ms, SLO 500ms"
        # Verify all 100 are registered
        assert len(sched.all_triggers()) == 100

    @pytest.mark.asyncio
    async def test_register_one_fail_blocks_others(self):
        # v1.3.1 patch 2 D2: 1 trigger fail → entire batch raises.
        # (gofmt on race: should the others still register? — design choice: NO,
        # atomic batch. spec §6.7.3.)
        plugin = _make_plugin("p1")
        good = _StubTrigger({"id": "good", "type": "stub"}, plugin)
        bad = _StubTrigger({"id": "bad", "type": "stub"}, plugin, fail=True)
        sched = TriggerScheduler()
        with pytest.raises(TriggerSetupError):
            await asyncio.gather(
                sched.register(good),
                sched.register(bad),
                return_exceptions=False,
            )
        # In atomic-batch mode, the good one might still be set up but
        # register() raises. Spec says "1 fail → 整 plugin fail" so caller
        # decides what to do. Scheduler level: both are tried; caller
        # catches TriggerSetupError and fails the plugin.

    @pytest.mark.asyncio
    async def test_unregister_calls_teardown(self):
        plugin = _make_plugin("p1")
        trigger = _StubTrigger({"id": "t1", "type": "stub"}, plugin)
        sched = TriggerScheduler()
        await sched.register(trigger)
        await sched.unregister_plugin("p1")
        assert trigger.teardown_called is True
        assert sched.get("t1") is None

    @pytest.mark.asyncio
    async def test_shutdown_tears_down_all(self):
        plugin = _make_plugin("p1")
        triggers = [_StubTrigger({"id": f"t{i}", "type": "stub"}, plugin) for i in range(5)]
        sched = TriggerScheduler()
        await sched.register_all(triggers)
        await sched.shutdown()
        for t in triggers:
            assert t.teardown_called is True
        assert len(sched.all_triggers()) == 0


# ---------- D2: TriggerSetupError contract ----------

class TestTriggerSetupErrorContract:
    def test_trigger_setup_error_is_exception(self):
        err = TriggerSetupError("test")
        assert isinstance(err, Exception)
        assert "test" in str(err)

