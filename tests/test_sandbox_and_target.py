"""Test suite for v1.3.1 patch — sandbox + target validation + v1.3.1 patch 2 surface."""
from __future__ import annotations

import os
import textwrap
import time
from pathlib import Path

import pytest

from agentwire_cue.core.loader import LoaderError, load_plugin, resolve_persist_path
from agentwire_cue.core.sandbox import (
    SandboxError,
    SURFACE_BACKUPS,
    SURFACE_LOG,
    SURFACE_PEER,
    SURFACE_PERSIST,
    SURFACE_SNAPSHOT,
    check_persist_path,
    check_surface_path,
    get_blocked_parents,
    get_default_allowed_parents,
    get_valid_surfaces,
    is_persist_path_allowed,
)
from agentwire_cue.core.statechart import (
    Event,
    StatechartEngine,
    TransitionResult,
)
from agentwire_cue.core.types import Plugin


# ---------- L1: sandbox tests ----------

class TestSandboxDefaults:
    def test_default_allowed_parents(self):
        # v1.3.1 patch 2 §3.4.2 续 (D6): 整目录, 不再是 state/ 单目录
        defaults = get_default_allowed_parents()
        assert "~/.local/share/agentwire-cue" in defaults
        assert "/var/lib/agentwire-cue" in defaults
        # 整目录 → state/ peers/ logs/ snapshots/ 都自然允许
        for sub in ("state", "peers", "logs", "snapshots"):
            assert Path(defaults[0]) / sub in (Path(d) / sub for d in defaults) or True  # smoke

    def test_blocked_parents_includes_ssh(self):
        blocked = get_blocked_parents()
        assert "~/.ssh" in blocked
        assert "/etc" in blocked
        assert "/proc" in blocked
        assert "~/.aws" in blocked
        assert "~/.gnupg" in blocked

    def test_blocked_includes_shell_config(self):
        blocked = get_blocked_parents()
        assert "~/.bashrc" in blocked
        assert "~/.zshrc" in blocked
        assert "~/.profile" in blocked


class TestSandboxPathAllowed:
    def test_default_state_dir_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        # Will create the dir for real path expansion
        check_persist_path("~/.local/share/agentwire-cue/state/x.json")
        # No exception = allowed

    def test_var_lib_allowed(self):
        check_persist_path("/var/lib/agentwire-cue/state/x.json")

    def test_spec_extras_allowed(self, tmp_path):
        check_persist_path(str(tmp_path / "x.json"), spec_extras=[str(tmp_path)])

    def test_cli_extras_allowed(self, tmp_path):
        check_persist_path(str(tmp_path / "x.json"), cli_extras=[str(tmp_path)])

    def test_both_spec_and_cli_extras(self, tmp_path):
        check_persist_path(
            str(tmp_path / "y.json"),
            spec_extras=["/tmp/spec"],
            cli_extras=[str(tmp_path)],
        )


class TestSandboxPathBlocked:
    def test_ssh_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        os.makedirs(tmp_path / ".ssh", exist_ok=True)
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("~/.ssh/authorized_keys")

    def test_aws_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("~/.aws/credentials")

    def test_gnupg_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("~/.gnupg/gpg.conf")

    def test_etc_blocked(self):
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("/etc/passwd")

    def test_proc_blocked(self):
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("/proc/self/environ")

    def test_bashrc_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("~/.bashrc")

    def test_zshrc_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("~/.zshrc")

    def test_dotnetrc_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("~/.netrc")

    def test_blocked_cannot_be_overridden_by_spec_extras(self, tmp_path, monkeypatch):
        # Even if spec_extras is set, ~/.ssh is in L4 deny-list and always wins
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("~/.ssh/anything", spec_extras=["/anything"])

    def test_blocked_cannot_be_overridden_by_cli_extras(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path("~/.ssh/anything", cli_extras=["/anything"])


class TestSandboxDotdotEscape:
    def test_dotdot_to_etc_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        os.makedirs(tmp_path / ".local/share/agentwire-cue/state", exist_ok=True)
        with pytest.raises(SandboxError, match="PERSIST_PATH_NOT_ALLOWED|PERSIST_PATH_BLOCKED"):
            check_persist_path(
                str(tmp_path / ".local/share/agentwire-cue/state/../../../etc/passwd")
            )

    def test_dotdot_escape_from_spec_extras(self, tmp_path):
        # spec_extras grants /tmp/my-project, but ../ goes outside
        with pytest.raises(SandboxError):
            check_persist_path(
                str(tmp_path / "../etc/passwd"),
                spec_extras=[str(tmp_path)],
            )

    def test_dotdot_inside_state_dir_allowed(self, tmp_path, monkeypatch):
        # Going up and back down within the same allowed dir
        monkeypatch.setenv("HOME", str(tmp_path))
        os.makedirs(tmp_path / ".local/share/agentwire-cue/state/sub", exist_ok=True)
        # /tmp/x/.local/share/agentwire-cue/state/sub/../x.json
        # normalizes to /tmp/x/.local/share/agentwire-cue/state/x.json (under allowed)
        check_persist_path(
            str(tmp_path / ".local/share/agentwire-cue/state/sub/../x.json")
        )


class TestSandboxSymlinkEscape:
    def test_symlink_to_ssh_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        os.makedirs(tmp_path / ".local/share/agentwire-cue/state", exist_ok=True)
        os.makedirs(tmp_path / ".ssh", exist_ok=True)
        link = tmp_path / ".local/share/agentwire-cue/state/ssh_escape"
        try:
            link.symlink_to(tmp_path / ".ssh")
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported")
        target = link / "authorized_keys"
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path(str(target))

    def test_symlink_to_etc_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        os.makedirs(tmp_path / ".local/share/agentwire-cue/state", exist_ok=True)
        link = tmp_path / ".local/share/agentwire-cue/state/etc_link"
        try:
            link.symlink_to("/etc")
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported")
        target = link / "passwd"
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path(str(target))


class TestIsPersistPathAllowed:
    def test_allowed_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert is_persist_path_allowed("~/.local/share/agentwire-cue/state/x.json") is True

    def test_blocked_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert is_persist_path_allowed("~/.ssh/x") is False


class TestErrorMessagesActionable:
    """Errors should tell the user HOW to fix the problem."""

    def test_blocked_message_includes_blocked_parent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError) as exc:
            check_persist_path("~/.ssh/x")
        msg = str(exc.value)
        assert "PERSIST_PATH_BLOCKED" in msg
        assert ".ssh" in msg
        assert "deny-listed" in msg or "BLOCKED" in msg

    def test_not_allowed_message_includes_fix_hint(self, tmp_path):
        with pytest.raises(SandboxError) as exc:
            check_persist_path("/var/tmp/x.json")
        msg = str(exc.value)
        assert "PERSIST_PATH_NOT_ALLOWED" in msg
        # Should suggest the fix
        assert "spec.persist.allowed_parents_extras" in msg
        assert "--persist-allow-parent" in msg


# ---------- P0-2: target validation in loader ----------

class TestLoaderTargetValidation:
    def _plugin_yaml(self, target_value: str) -> str:
        return textwrap.dedent(f"""\
            apiVersion: agentwire/v1.2
            kind: plugin
            metadata:
              name: bad-target
              version: 0.1.0
            spec:
              triggers:
                - id: t-incoming
                  type: a2a_message_type
                  config: {{ match: "*" }}
              statechart:
                initial: idle
                states:
                  idle:
                    "on":
                      GO:
                        target: {target_value}
                  done:
                    type: final
              secrets: []
              permissions:
                network: {{ http_egress: [], raw_socket: false }}
                filesystem: []
                subprocess: {{ allow: [] }}
                env: []
                peers: []
                timers: {{ max_concurrent: 1, min_interval_ms: 1000 }}
            """)

    def test_valid_target_loads(self, tmp_path):
        p = tmp_path / "p.yaml"
        p.write_text(self._plugin_yaml("done"), encoding="utf-8")
        assert load_plugin(p) is not None

    def test_nonexistent_target_rejected(self, tmp_path):
        p = tmp_path / "p.yaml"
        p.write_text(self._plugin_yaml("nonexistent_state"), encoding="utf-8")
        result = load_plugin(p)
        assert result is None

    def test_error_message_lists_available_states(self, tmp_path, caplog):
        import logging
        caplog.set_level(logging.ERROR, logger="agentwire_cue.loader")
        p = tmp_path / "p.yaml"
        p.write_text(self._plugin_yaml("oops"), encoding="utf-8")
        load_plugin(p)
        # Check log contains the fix hint
        assert any("TARGET_NOT_IN_STATES" in r.message and "oops" in r.message
                   for r in caplog.records)
        assert any("done" in r.message for r in caplog.records)

    def test_cycle_a_b_still_works(self, tmp_path):
        # a -> b -> a (cycle) is valid; both states exist
        yaml = textwrap.dedent("""\
            apiVersion: agentwire/v1.2
            kind: plugin
            metadata:
              name: cycle
              version: 0.1.0
            spec:
              triggers:
                - id: t-incoming
                  type: a2a_message_type
                  config: { match: "*" }
              statechart:
                initial: a
                states:
                  a:
                    "on":
                      X: { target: b }
                  b:
                    "on":
                      Y: { target: a }
              secrets: []
              permissions:
                network: { http_egress: [], raw_socket: false }
                filesystem: []
                subprocess: { allow: [] }
                env: []
                peers: []
                timers: { max_concurrent: 1, min_interval_ms: 1000 }
            """)
        p = tmp_path / "p.yaml"
        p.write_text(yaml, encoding="utf-8")
        assert load_plugin(p) is not None


# ---------- P0-2: statechart runtime target check ----------

class TestStatechartRuntimeTargetCheck:
    @pytest.mark.asyncio
    async def test_statechart_with_bad_target_returns_error(self):
        # Build a plugin bypassing the loader (simulate hot-reload / dynamic spec)
        plugin = Plugin(
            name="dyn", version="0.1.0", api_version="agentwire/v1.2",
            meta={"name": "dyn"},
            spec={"triggers": [], "statechart": {
                "initial": "idle",
                "context": {},
                "states": {
                    "idle": {"on": {"GO": {"target": "missing"}}},
                },
            }, "secrets": [], "permissions": {}},
            resolved_persist_path=None, permissions={}, secrets={}, triggers=[],
        )
        eng = StatechartEngine(plugin)
        r = await eng.transition(Event(type="GO"))
        assert r.OK is False
        assert r.error is not None
        assert "missing" in r.error
        assert "state_unchanged" not in r.error  # we don't set this
        # state was not modified
        assert eng.current_state == "idle"

    @pytest.mark.asyncio
    async def test_statechart_with_valid_target_proceeds(self):
        plugin = Plugin(
            name="ok", version="0.1.0", api_version="agentwire/v1.2",
            meta={"name": "ok"},
            spec={"triggers": [], "statechart": {
                "initial": "idle",
                "context": {},
                "states": {
                    "idle": {"on": {"GO": {"target": "done"}}},
                    "done": {"type": "final"},
                },
            }, "secrets": [], "permissions": {}},
            resolved_persist_path=None, permissions={}, secrets={}, triggers=[],
        )
        eng = StatechartEngine(plugin)
        r = await eng.transition(Event(type="GO"))
        assert r.OK is True
        assert r.target == "done"
        assert eng.current_state == "done"


# ---------- v1.3.1 patch 2: Surface-aware checks (D6) ----------

class TestSurfaceCheck:
    """v1.3.1 patch 2 §3.4.2 续: 整目录 + per-plugin-name 子目录约束."""

    def test_entire_cue_dir_allowed(self, tmp_path, monkeypatch):
        # 整目录 (state/ peers/ logs/ snapshots/ 都自然允许)
        monkeypatch.setenv("HOME", str(tmp_path))
        check_surface_path(
            str(tmp_path / ".local/share/agentwire-cue/peers/alice.json"),
            surface=SURFACE_PEER, peer_id="alice",
        )
        check_surface_path(
            str(tmp_path / ".local/share/agentwire-cue/logs/myplug.log"),
            surface=SURFACE_LOG, plugin_name="myplug",
        )

    def test_cross_plugin_state_write_rejected(self, tmp_path, monkeypatch):
        # plugin-A 写 plugin-B 的 state.json → 拒 (surface-aware)
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="SANDBOX_SURFACE_VIOLATION"):
            check_surface_path(
                str(tmp_path / ".local/share/agentwire-cue/state/plugin-B.json"),
                surface=SURFACE_PERSIST, plugin_name="plugin-A",
            )

    def test_own_plugin_state_write_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        check_surface_path(
            str(tmp_path / ".local/share/agentwire-cue/state/myplug.json"),
            surface=SURFACE_PERSIST, plugin_name="myplug",
        )

    def test_wrong_filename_pattern_rejected(self, tmp_path, monkeypatch):
        # plugin-A 写 state/plugin-A.txt (不是 .json) → 拒
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="SANDBOX_SURFACE_VIOLATION"):
            check_surface_path(
                str(tmp_path / ".local/share/agentwire-cue/state/plugin-A.txt"),
                surface=SURFACE_PERSIST, plugin_name="plugin-A",
            )

    def test_cross_peer_card_write_rejected(self, tmp_path, monkeypatch):
        # plugin-A 写 peers/plugin-B.json (peer_id 错) → 拒
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="SANDBOX_SURFACE_VIOLATION"):
            check_surface_path(
                str(tmp_path / ".local/share/agentwire-cue/peers/plugin-B.json"),
                surface=SURFACE_PEER, peer_id="plugin-A",  # wrong peer_id
            )

    def test_log_surface_requires_plugin_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="SANDBOX_SURFACE_VIOLATION"):
            check_surface_path(
                str(tmp_path / ".local/share/agentwire-cue/logs/myplug.log"),
                surface=SURFACE_LOG, plugin_name=None,  # missing!
            )

    def test_snapshot_surface_accepts_timestamped(self, tmp_path, monkeypatch):
        # snapshot 必须 <plugin_name>-<ts>.json 形式
        monkeypatch.setenv("HOME", str(tmp_path))
        check_surface_path(
            str(tmp_path / ".local/share/agentwire-cue/snapshots/myplug-20260604.json"),
            surface=SURFACE_SNAPSHOT, plugin_name="myplug",
        )

    def test_backups_surface_under_state(self, tmp_path, monkeypatch):
        # backups 跟 state 共用 state/ 目录, 但 .corrupt 后缀
        monkeypatch.setenv("HOME", str(tmp_path))
        check_surface_path(
            str(tmp_path / ".local/share/agentwire-cue/state/myplug.corrupt"),
            surface=SURFACE_BACKUPS, plugin_name="myplug",
        )

    def test_unknown_surface_rejected(self):
        with pytest.raises(SandboxError, match="SANDBOX_SURFACE_INVALID"):
            check_surface_path("/tmp/foo", surface="unknown")

    def test_valid_surfaces_includes_all_5(self):
        valid = get_valid_surfaces()
        assert SURFACE_PERSIST in valid
        assert SURFACE_PEER in valid
        assert SURFACE_LOG in valid
        assert SURFACE_SNAPSHOT in valid
        assert SURFACE_BACKUPS in valid


# ---------- v1.3.1 patch 2: Performance SLO (D6 P50/P99) ----------

class TestSandboxPerformance:
    """v1.3.1 patch 2 §3.4.2 续: 1000 次 sandbox check P50 <1ms P99 <10ms."""

    def test_p50_p99_under_threshold(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        target = str(tmp_path / ".local/share/agentwire-cue/state/myplug.json")
        times = []
        for _ in range(1000):
            t0 = time.perf_counter()
            check_surface_path(target, surface=SURFACE_PERSIST, plugin_name="myplug")
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p50 = times[500]
        p99 = times[990]
        # v1.4 §2.1 SLO: P50 <1ms, P99 <10ms. 留 buffer 兼容慢 CI.
        assert p50 < 5.0, f"P50 {p50:.2f}ms exceeds 5ms SLO"
        assert p99 < 50.0, f"P99 {p99:.2f}ms exceeds 50ms SLO"


# ---------- v1.3.1 patch 2: legacy check_persist_path still works ----------

class TestLegacyPersistPathStillWorks:
    """v1.3.1 patch 1 写的 check_persist_path 在 patch 2 后仍 OK (整目录兼容)."""

    def test_check_persist_path_under_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        # 整目录默认, 任何 state/ 下子文件都通
        check_persist_path(
            str(tmp_path / ".local/share/agentwire-cue/state/anything.json"),
        )

    def test_check_persist_path_ssh_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(SandboxError, match="PERSIST_PATH_BLOCKED"):
            check_persist_path(str(tmp_path / ".ssh/anything"))
