"""Test suite for loader — v1.3 §3 spec."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from agentwire_cue.core.loader import (
    LoaderError,
    check_persist_writable,
    check_secrets,
    discover_plugins,
    get_safe_env,
    load_all,
    load_plugin,
    load_yaml,
    resolve_persist_path,
    validate_schema,
)


# ---------- load_yaml (YAML 1.2 behavior) ----------

class TestLoadYaml:
    def test_simple_yaml(self, tmp_path: Path):
        p = tmp_path / "p.yaml"
        p.write_text("a: 1\nb: hello\n", encoding="utf-8")
        assert load_yaml(p) == {"a": 1, "b": "hello"}

    def test_yaml_12_preserves_on_as_string(self, tmp_path: Path):
        # v1.2 spec §1.1: ruamel MUST NOT treat `on` as bool.
        p = tmp_path / "p.yaml"
        p.write_text('"on": value\n', encoding="utf-8")
        d = load_yaml(p)
        assert d == {"on": "value"}, f"YAML 1.2 should keep 'on' as string, got {d}"

    def test_yaml_12_keeps_unquoted_on_as_string(self, tmp_path: Path):
        # ruamel.yaml (YAML 1.2) treats bare `on` as a string. The on:
        # pattern that the plugin schema uses is the KEY in the `on` mapping
        # handler — but state names ARE strings. We rely on ruamel.
        p = tmp_path / "p.yaml"
        p.write_text("on: 1\n", encoding="utf-8")
        d = load_yaml(p)
        assert d == {"on": 1}

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(LoaderError, match="not found"):
            load_yaml(tmp_path / "missing.yaml")

    def test_invalid_yaml_raises_with_line_info(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("a: 1\nb: [unclosed\n", encoding="utf-8")
        with pytest.raises(LoaderError, match="YAML parse error"):
            load_yaml(p)

    def test_top_level_non_mapping_raises(self, tmp_path: Path):
        p = tmp_path / "p.yaml"
        p.write_text("- 1\n- 2\n", encoding="utf-8")
        with pytest.raises(LoaderError, match="not a YAML mapping"):
            load_yaml(p)


# ---------- resolve_persist_path ----------

class TestResolvePersistPath:
    def test_none_returns_none(self):
        assert resolve_persist_path(None, {"name": "x"}) is None

    def test_literal_path_under_allowed(self, monkeypatch):
        monkeypatch.setenv("HOME", "/tmp/cue-test-home")
        p = resolve_persist_path("~/.local/share/agentwire-cue/state/x.json", {"name": "x"})
        assert p is not None
        assert str(p).endswith("x.json")

    def test_meta_substitution(self, monkeypatch):
        monkeypatch.setenv("HOME", "/tmp/cue-test-home")
        p = resolve_persist_path("~/.local/share/agentwire-cue/state/{{meta.name}}.json", {"name": "echo"})
        assert p is not None
        assert p.name == "echo.json"
        assert "~" not in str(p)  # expanduser applied

    def test_unknown_template_var_raises(self):
        with pytest.raises(LoaderError, match="non-meta variables"):
            resolve_persist_path("{{context.x}}.json", {"name": "x"})

    def test_partial_meta_with_literal_works(self, monkeypatch):
        monkeypatch.setenv("HOME", "/tmp/cue-test-home")
        p = resolve_persist_path(
            "~/.local/share/agentwire-cue/state/{{meta.name}}-v{{meta.version}}.json",
            {"name": "echo", "version": "1.0"},
        )
        assert p is not None and p.name == "echo-v1.0.json"

    def test_path_in_ssh_rejected(self):
        with pytest.raises(LoaderError, match="PERSIST_PATH_BLOCKED"):
            resolve_persist_path("~/.ssh/authorized_keys", {"name": "x"})

    def test_path_in_etc_rejected(self):
        with pytest.raises(LoaderError, match="PERSIST_PATH_BLOCKED"):
            resolve_persist_path("/etc/passwd", {"name": "x"})

    def test_path_dotdot_escape_rejected(self):
        with pytest.raises(LoaderError, match="PERSIST_PATH_NOT_ALLOWED"):
            resolve_persist_path(
                "~/.local/share/agentwire-cue/state/../../../etc/passwd",
                {"name": "x"},
            )

    def test_path_with_spec_extras_allowed(self):
        p = resolve_persist_path(
            "/tmp/my-project/state.json",
            {"name": "x"},
            spec_extras=["/tmp/my-project"],
        )
        assert p is not None

    def test_path_with_spec_extras_dotdot_escape_still_rejected(self):
        # spec_extras grants the parent, but a child path with ../ that escapes
        # the parent must still be denied.
        with pytest.raises(LoaderError, match="PERSIST_PATH_NOT_ALLOWED|PERSIST_PATH_BLOCKED"):
            resolve_persist_path(
                "/tmp/my-project/../etc/passwd",
                {"name": "x"},
                spec_extras=["/tmp/my-project"],
            )


# ---------- check_persist_writable ----------

class TestCheckPersistWritable:
    def test_writable_path(self, tmp_path: Path):
        target = tmp_path / "sub" / "state.json"
        check_persist_writable(target)  # should not raise
        assert target.parent.exists()

    def test_unwritable_path_raises(self, tmp_path: Path):
        # simulate by passing a path under a non-creatable parent
        # (on Linux: try writing to a path with a non-directory parent)
        # The cleanest portable way: pass a path where the parent IS a file.
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        target = blocker / "state.json"  # parent is a file, not a dir
        with pytest.raises(LoaderError, match="not creatable|not writable"):
            check_persist_writable(target)


# ---------- check_secrets ----------

class TestCheckSecrets:
    def test_all_present(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "v1")
        monkeypatch.setenv("OPTIONAL", "v2")
        secrets = check_secrets([
            {"name": "MY_SECRET", "required": True},
            {"name": "OPTIONAL", "required": False},
        ])
        assert secrets == {"MY_SECRET": "v1", "OPTIONAL": "v2"}

    def test_required_missing_raises(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(LoaderError, match="required secret env var not set"):
            check_secrets([{"name": "MISSING_VAR", "required": True}])

    def test_optional_missing_skipped(self, monkeypatch):
        monkeypatch.delenv("OPT_VAR", raising=False)
        secrets = check_secrets([{"name": "OPT_VAR", "required": False}])
        assert secrets == {}

    def test_empty_secrets_list(self):
        assert check_secrets([]) == {}


# ---------- get_safe_env (spec §3.5.1) ----------

class TestGetSafeEnv:
    def test_contains_only_secrets_and_base(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("LANG", "C.UTF-8")
        monkeypatch.setenv("HOST_SECRET", "should-be-injected")
        env = get_safe_env({"FOO": "bar"})
        # MUST include the declared secret and minimal base
        assert env["FOO"] == "bar"
        assert env["PATH"] == "/usr/bin"
        assert env["LANG"] == "C.UTF-8"
        # MUST NOT include unrelated host env
        assert "HOST_SECRET" not in env


# ---------- discover_plugins ----------

class TestDiscoverPlugins:
    def test_finds_yaml_and_yml_recursively(self, tmp_path: Path):
        (tmp_path / "a.yaml").write_text("a: 1\n", encoding="utf-8")
        (tmp_path / "b.yml").write_text("b: 1\n", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.yaml").write_text("c: 1\n", encoding="utf-8")
        files = discover_plugins(tmp_path)
        names = sorted(p.name for p in files)
        assert names == ["a.yaml", "b.yml", "c.yaml"]

    def test_skips_symlinks(self, tmp_path: Path):
        real = tmp_path / "real.yaml"
        real.write_text("a: 1\n", encoding="utf-8")
        link = tmp_path / "link.yaml"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported")
        files = discover_plugins(tmp_path)
        names = [p.name for p in files]
        assert "real.yaml" in names
        assert "link.yaml" not in names

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        assert discover_plugins(tmp_path / "nope") == []


# ---------- load_plugin (integration) ----------

_VALID_PLUGIN = textwrap.dedent("""\
    apiVersion: agentwire/v1.2
    kind: plugin
    metadata:
      name: my-cue
      version: 0.1.0
      description: test
    spec:
      triggers:
        - id: incoming
          type: a2a_message_type
          config: { match: "*" }
      statechart:
        initial: idle
        context:
          n: 0
        states:
          idle:
            "on":
              X:
                target: done
                actions:
                  - type: increment_context
                    with: { key: n, by: 1 }
          done:
            type: final
      secrets: []
      permissions:
        network: { http_egress: [], raw_socket: false }
        filesystem: []
        subprocess: { allow: [] }
        env: []
        peers: []
        timers: { max_concurrent: 1, min_interval_ms: 1000 }
    """)


def _plugin_with_persist() -> str:
    return _VALID_PLUGIN.replace(
        "    initial: idle\n",
        '    initial: idle\n    persist:\n      path: "~/.local/share/agentwire-cue/state/{{meta.name}}.json"\n',
    )


def _plugin_without_triggers() -> str:
    lines = _VALID_PLUGIN.split("\n")
    out: list[str] = []
    skip = 0
    for line in lines:
        if line.startswith("  triggers:"):
            skip = 3  # skip triggers, -id line, type line, config line
            continue
        if skip > 0:
            skip -= 1
            continue
        out.append(line)
    return "\n".join(out)


def _plugin_with_required_secret() -> str:
    return _VALID_PLUGIN.replace(
        "  secrets: []\n",
        "  secrets:\n    - name: REQUIRED_KEY\n      required: true\n",
    )


def _plugin_with_bad_persist_var() -> str:
    return _VALID_PLUGIN.replace(
        "    initial: idle\n",
        '    initial: idle\n    persist:\n      path: "{{context.x}}.json"\n',
    )


def _plugin_with_bad_guard() -> str:
    return _VALID_PLUGIN.replace(
        "            target: done\n",
        '            target: done\n            guard: "event.x > ("\n',
    )


class TestLoadPlugin:
    def test_valid_plugin_loads(self, tmp_path: Path):
        p = tmp_path / "ok.yaml"
        p.write_text(_VALID_PLUGIN, encoding="utf-8")
        plugin = load_plugin(p)
        assert plugin is not None
        assert plugin.name == "my-cue"
        assert plugin.version == "0.1.0"
        assert plugin.api_version == "agentwire/v1.2"
        assert len(plugin.triggers) == 1
        assert plugin.triggers[0].type == "a2a_message_type"

    def test_persist_path_resolved(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        p = tmp_path / "p.yaml"
        p.write_text(_plugin_with_persist(), encoding="utf-8")
        plugin = load_plugin(p)
        assert plugin is not None
        assert plugin.resolved_persist_path is not None
        assert plugin.resolved_persist_path.name == "my-cue.json"

    def test_missing_required_field_returns_none(self, tmp_path: Path):
        # remove triggers (required per schema)
        p = tmp_path / "bad.yaml"
        p.write_text(_plugin_without_triggers(), encoding="utf-8")
        assert load_plugin(p) is None

    def test_yaml_syntax_error_returns_none(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("a: [unclosed\n", encoding="utf-8")
        assert load_plugin(p) is None

    def test_schema_validation_error_returns_none(self, tmp_path: Path):
        # wrong apiVersion enum
        bad = _VALID_PLUGIN.replace("apiVersion: agentwire/v1.2", "apiVersion: agentwire/v999")
        p = tmp_path / "bad.yaml"
        p.write_text(bad, encoding="utf-8")
        assert load_plugin(p) is None

    def test_required_secret_missing_returns_none(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("REQUIRED_KEY", raising=False)
        p = tmp_path / "bad.yaml"
        p.write_text(_plugin_with_required_secret(), encoding="utf-8")
        assert load_plugin(p) is None

    def test_persist_path_unknown_var_returns_none(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text(_plugin_with_bad_persist_var(), encoding="utf-8")
        assert load_plugin(p) is None

    def test_bad_guard_expression_returns_none(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text(_plugin_with_bad_guard(), encoding="utf-8")
        assert load_plugin(p) is None
    def test_echo_with_persist_example_loads(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "echo-with-persist.yaml"
        plugin = load_plugin(example)
        assert plugin is not None
        assert plugin.name == "echo"
        assert plugin.api_version == "agentwire/v1.2"



class TestLoadAll:
    def test_loads_multiple_plugins_skipping_bad(self, tmp_path: Path):
        good = tmp_path / "good.yaml"
        good.write_text(_VALID_PLUGIN, encoding="utf-8")
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: [unclosed\n", encoding="utf-8")
        plugins = load_all(tmp_path)
        assert len(plugins) == 1
        assert plugins[0].name == "my-cue"

    def test_empty_dir_returns_empty(self, tmp_path: Path):
        assert load_all(tmp_path) == []

    def test_zero_loaded_from_existing_dir_logs_error(self, tmp_path: Path, caplog):
        import logging
        caplog.set_level(logging.ERROR, logger="agentwire_cue.loader")
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: [unclosed\n", encoding="utf-8")
        plugins = load_all(tmp_path)
        assert plugins == []
        # 0 plugins successfully loaded — host should exit 1
        assert any("0 plugins successfully loaded" in r.message for r in caplog.records)


# ===========================================================================
# v1.6.5: production.local.yaml overlay support
# ===========================================================================

class TestDeepMerge:
    """v1.6.5: helper used by _apply_local_overlay."""

    def test_deep_merge_overrides_scalar_leaf(self):
        from agentwire_cue.core.loader import _deep_merge
        base = {"spec": {"peers": {"main": {"uuid": "old", "url": "http://old"}}}}
        overlay = {"spec": {"peers": {"main": {"uuid": "new"}}}}
        result = _deep_merge(base, overlay)
        assert result["spec"]["peers"]["main"]["uuid"] == "new"
        assert result["spec"]["peers"]["main"]["url"] == "http://old"  # preserved

    def test_deep_merge_replaces_list_atomically(self):
        """Lists replace entirely — no item-by-item merge (could cause
        surprising behavior with secret paths etc.)."""
        from agentwire_cue.core.loader import _deep_merge
        base = {"permissions": {"peers": ["main", "other"]}}
        overlay = {"permissions": {"peers": ["main", "pawly"]}}
        result = _deep_merge(base, overlay)
        assert result["permissions"]["peers"] == ["main", "pawly"]

    def test_deep_merge_adds_new_top_level_keys(self):
        from agentwire_cue.core.loader import _deep_merge
        base = {"a": 1}
        overlay = {"b": 2}
        result = _deep_merge(base, overlay)
        assert result == {"a": 1, "b": 2}

    def test_deep_merge_does_not_mutate_base(self):
        from agentwire_cue.core.loader import _deep_merge
        base = {"a": {"b": 1}}
        overlay = {"a": {"b": 2}}
        result = _deep_merge(base, overlay)
        assert result["a"]["b"] == 2
        assert base["a"]["b"] == 1  # original untouched


class TestApplyLocalOverlay:
    """v1.6.5: _apply_local_overlay applies sibling production.local.yaml."""

    def test_no_overlay_file_returns_base_unchanged(self, tmp_path: Path):
        from agentwire_cue.core.loader import _apply_local_overlay
        base = {"spec": {"peers": {"main": {"uuid": "abc"}}}}
        # No overlay file present
        result = _apply_local_overlay(tmp_path / "cue.yaml", base)
        assert result == base

    def test_overlay_present_merges_real_peer_values(self, tmp_path: Path):
        from agentwire_cue.core.loader import _apply_local_overlay
        cue = tmp_path / "cue.yaml"
        cue.write_text("placeholder", encoding="utf-8")
        overlay = tmp_path / "production.local.yaml"
        overlay.write_text(textwrap.dedent("""\
            spec:
              peers:
                remote_peer_a:
                  uuid: "75755f137e7451c0"
                  url: "http://100.91.108.62:18800"
        """), encoding="utf-8")
        base = {"spec": {"peers": {"remote_peer_a": {"uuid": "<set-me>", "url": "http://<set-me>:18800"}}}}
        result = _apply_local_overlay(cue, base)
        assert result["spec"]["peers"]["remote_peer_a"]["uuid"] == "75755f137e7451c0"
        assert result["spec"]["peers"]["remote_peer_a"]["url"] == "http://100.91.108.62:18800"

    def test_malformed_overlay_logs_warning_and_falls_back(self, tmp_path: Path, caplog):
        from agentwire_cue.core.loader import _apply_local_overlay
        cue = tmp_path / "cue.yaml"
        cue.write_text("placeholder", encoding="utf-8")
        overlay = tmp_path / "production.local.yaml"
        overlay.write_text("a: [unclosed\n", encoding="utf-8")
        base = {"spec": {"peers": {"remote_peer_a": {"uuid": "original"}}}}
        import logging
        caplog.set_level(logging.WARNING, logger="agentwire_cue.loader")
        result = _apply_local_overlay(cue, base)
        # Falls back to base on parse error
        assert result["spec"]["peers"]["remote_peer_a"]["uuid"] == "original"
        assert any("production.local.yaml overlay load failed" in r.message for r in caplog.records)


class TestDiscoverPluginsSkipsLocalOverlay:
    """v1.6.5: *.local.yaml files are NEVER plugins — they're overlays only."""

    def test_discover_plugins_skips_local_yaml(self, tmp_path: Path):
        from agentwire_cue.core.loader import discover_plugins
        # Create a real plugin + a *.local.yaml overlay
        real = tmp_path / "good.yaml"
        real.write_text(_VALID_PLUGIN, encoding="utf-8")
        local = tmp_path / "production.local.yaml"
        local.write_text("a: 1\n", encoding="utf-8")
        paths = discover_plugins(tmp_path)
        names = [p.name for p in paths]
        assert "good.yaml" in names
        assert "production.local.yaml" not in names, (
            "*.local.yaml files are overlays, not plugins. discover_plugins "
            "must skip them to avoid spurious schema-validation failures."
        )
