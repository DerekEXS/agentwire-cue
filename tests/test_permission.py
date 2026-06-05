"""Test suite for permission enforcer — v1.3 §8 spec."""
from __future__ import annotations

import pytest

from agentwire_cue.core.permission import (
    PermissionDecision,
    PermissionEnforcer,
    PermissionError_,
    _ALLOWED_URL_SCHEMES,
)


# ---------- registration ----------

class TestRegister:
    def test_register_and_get(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "network": {"http_egress": ["api.example.com"], "raw_socket": True},
            "filesystem": [{"path": "/tmp/*", "modes": ["write"]}],
        })
        perms = enforcer.get("plug")
        assert perms["network"]["http_egress"] == ["api.example.com"]
        assert perms["network"]["raw_socket"] is True

    def test_register_defaults_fill_missing_keys(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {})  # nothing specified
        perms = enforcer.get("plug")
        # defaults
        assert perms["network"]["http_egress"] == []
        assert perms["network"]["raw_socket"] is False
        assert perms["filesystem"] == []
        assert perms["subprocess"]["allow"] == []
        assert perms["env"] == []
        assert perms["peers"] == []
        assert perms["timers"]["max_concurrent"] == 1

    def test_duplicate_register_raises(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {})
        with pytest.raises(PermissionError_, match="already registered"):
            enforcer.register("plug", {})

    def test_unregister_removes(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {})
        enforcer.unregister("plug")
        assert enforcer.get("plug") == {}


# ---------- network.http_egress + URL scheme ----------

class TestNetwork:
    def test_http_allowed_for_matching_host(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": ["api.example.com"]}})
        d = enforcer.check_network_http("plug", "https://api.example.com/x")
        assert d.allowed is True

    def test_http_denied_for_non_matching_host(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": ["api.example.com"]}})
        d = enforcer.check_network_http("plug", "https://other.com/x")
        assert d.allowed is False
        assert "other.com" in d.detail

    def test_glob_pattern_matches(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": ["*.example.com"]}})
        assert enforcer.check_network_http("plug", "https://a.example.com/").allowed
        assert enforcer.check_network_http("plug", "https://b.example.com/").allowed
        assert enforcer.check_network_http("plug", "https://example.com/").allowed is False

    def test_url_scheme_file_blocked(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": ["*"]}})  # wildcard host
        d = enforcer.check_network_http("plug", "file:///etc/passwd")
        assert d.allowed is False
        assert "file" in d.detail

    def test_url_scheme_javascript_blocked(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": ["*"]}})
        d = enforcer.check_network_http("plug", "javascript:alert(1)")
        assert d.allowed is False

    def test_url_scheme_data_blocked(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": ["*"]}})
        d = enforcer.check_network_http("plug", "data:text/plain,abc")
        assert d.allowed is False

    def test_url_scheme_https_allowed(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": ["*"]}})
        d = enforcer.check_network_http("plug", "https://example.com")
        assert d.allowed is True

    def test_url_scheme_http_allowed(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": ["*"]}})
        d = enforcer.check_network_http("plug", "http://example.com")
        assert d.allowed is True

    def test_empty_host_list_denies_everything(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"http_egress": []}})
        d = enforcer.check_network_http("plug", "https://example.com")
        assert d.allowed is False


class TestRawSocket:
    def test_raw_socket_allowed_when_true(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"raw_socket": True}})
        d = enforcer.check_network_raw("plug")
        assert d.allowed is True

    def test_raw_socket_denied_when_false(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"network": {"raw_socket": False}})
        d = enforcer.check_network_raw("plug")
        assert d.allowed is False


# ---------- filesystem ----------

class TestFilesystem:
    def test_write_to_allowed_path(self, tmp_path: Path):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "filesystem": [{"path": str(tmp_path) + "/*", "modes": ["write", "read"]}],
            "persist": {"allowed_parents_extras": [str(tmp_path)]},
        })
        d = enforcer.check_filesystem("plug", str(tmp_path / "x.json"), "write")
        assert d.allowed is True

    def test_write_to_disallowed_path(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "filesystem": [{"path": "/tmp/*", "modes": ["read"]}]  # read only
        })
        d = enforcer.check_filesystem("plug", "/tmp/x.json", "write")
        assert d.allowed is False

    def test_unknown_mode_rejected(self, tmp_path: Path):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"filesystem": [{"path": str(tmp_path) + "/*", "modes": ["write"]}]})
        d = enforcer.check_filesystem("plug", str(tmp_path / "x"), "execute")
        assert d.allowed is False
        assert "unknown mode" in d.detail

    def test_no_filesystem_rules_denies_all(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"filesystem": []})
        d = enforcer.check_filesystem("plug", "/tmp/x", "write")
        assert d.allowed is False

    def test_home_dir_expansion(self, tmp_path: Path, monkeypatch):
        enforcer = PermissionEnforcer()
        # Use tmp_path as HOME; sandbox allows ~/.local/share/agentwire-cue/state/*
        monkeypatch.setenv("HOME", str(tmp_path))
        # register a rule that matches the sandboxed path
        enforcer.register("plug", {
            "filesystem": [{"path": str(tmp_path) + "/.local/share/agentwire-cue/state/*", "modes": ["write"]}],
        })
        d = enforcer.check_filesystem("plug", "~/.local/share/agentwire-cue/state/x.json", "write")
        assert d.allowed is True

    def test_sandbox_blocks_ssh_even_with_filesystem_rule(self, tmp_path: Path, monkeypatch):
        # v1.3.1 L3: even with a permissive filesystem rule, sandbox denies
        # writes to ~/.ssh/* (the path is blocked, not the rule)
        monkeypatch.setenv("HOME", str(tmp_path))
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "filesystem": [{"path": "*", "modes": ["write"]}]  # allow everything
        })
        d = enforcer.check_filesystem("plug", "~/.ssh/authorized_keys", "write")
        assert d.allowed is False
        assert "sandbox" in d.detail.lower() or "blocked" in d.detail.lower()

    def test_sandbox_blocks_etc(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "filesystem": [{"path": "*", "modes": ["write"]}]
        })
        d = enforcer.check_filesystem("plug", "/etc/passwd", "write")
        assert d.allowed is False

    def test_read_does_not_go_through_sandbox(self, tmp_path: Path):
        # v1.3.1: sandbox only blocks writes, not reads
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "filesystem": [{"path": "*", "modes": ["read"]}]
        })
        # Reading /etc/passwd is technically allowed (just filesystem rule matches)
        d = enforcer.check_filesystem("plug", "/etc/passwd", "read")
        assert d.allowed is True  # only writes are sandboxed


# ---------- subprocess ----------

class TestSubprocess:
    def test_allowed_binary(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"subprocess": {"allow": ["echo", "curl"]}})
        d = enforcer.check_subprocess("plug", ["echo", "hi"])
        assert d.allowed is True

    def test_glob_match(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"subprocess": {"allow": ["ffmpeg*"]}})
        assert enforcer.check_subprocess("plug", ["ffmpeg", "-i", "x"]).allowed
        assert enforcer.check_subprocess("plug", ["ffmpeg-pro", "x"]).allowed
        assert enforcer.check_subprocess("plug", ["curl", "x"]).allowed is False

    def test_empty_allow_denies_all(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"subprocess": {"allow": []}})
        assert enforcer.check_subprocess("plug", ["echo"]).allowed is False

    def test_empty_cmd_denied(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"subprocess": {"allow": ["*"]}})
        d = enforcer.check_subprocess("plug", [])
        assert d.allowed is False


# ---------- env ----------

class TestEnv:
    def test_allowed_var(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"env": ["MY_API_KEY", "TOKEN_*"]})
        d = enforcer.check_env("plug", "MY_API_KEY")
        assert d.allowed is True

    def test_glob_match(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"env": ["TOKEN_*"]})
        assert enforcer.check_env("plug", "TOKEN_FOO").allowed
        assert enforcer.check_env("plug", "OTHER").allowed is False


# ---------- peers ----------

class TestPeers:
    def test_allowed_peer(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "peers": [{"id": "alice", "allow_messages": ["MSG"]}]
        })
        d = enforcer.check_peer("plug", "alice", "MSG")
        assert d.allowed is True

    def test_peer_message_type_denied(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "peers": [{"id": "alice", "allow_messages": ["MSG"]}]
        })
        d = enforcer.check_peer("plug", "alice", "OTHER")
        assert d.allowed is False

    def test_wildcard_message_type(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {
            "peers": [{"id": "alice", "allow_messages": ["*"]}]
        })
        assert enforcer.check_peer("plug", "alice", "ANY").allowed

    def test_unknown_peer_denied(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"peers": []})
        d = enforcer.check_peer("plug", "alice")
        assert d.allowed is False


# ---------- timers ----------

class TestTimers:
    def test_within_limit_can_acquire(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"timers": {"max_concurrent": 2}})
        assert enforcer.acquire_timer_slot("plug") is True
        assert enforcer.acquire_timer_slot("plug") is True
        assert enforcer.acquire_timer_slot("plug") is False  # full

    def test_release_then_acquire(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"timers": {"max_concurrent": 1}})
        assert enforcer.acquire_timer_slot("plug") is True
        assert enforcer.acquire_timer_slot("plug") is False
        enforcer.release_timer_slot("plug")
        assert enforcer.acquire_timer_slot("plug") is True

    def test_release_at_zero_no_underflow(self):
        enforcer = PermissionEnforcer()
        enforcer.register("plug", {"timers": {"max_concurrent": 1}})
        enforcer.release_timer_slot("plug")
        enforcer.release_timer_slot("plug")
        # no exception; counter stays at 0
        assert enforcer.acquire_timer_slot("plug") is True


# ---------- sensitive filter (shared with persistence §9, admin §10) ----------

class TestFilterSensitive:
    def test_filters_token_secret_password_credential(self):
        enforcer = PermissionEnforcer()
        data = {
            "name": "ok",
            "auth_token": "secret",
            "API_SECRET": "secret",
            "user_password": "x",
            "credentials_file": "/x",
        }
        out = enforcer.filter_sensitive(data)
        assert out == {"name": "ok"}

    def test_keeps_unrelated_keys(self):
        enforcer = PermissionEnforcer()
        out = enforcer.filter_sensitive({"a": 1, "b": "x", "count": 5})
        assert out == {"a": 1, "b": "x", "count": 5}

    def test_extra_exclude(self):
        enforcer = PermissionEnforcer()
        out = enforcer.filter_sensitive(
            {"a": 1, "ephemeral": "x"},
            extra_exclude=["ephemeral"],
        )
        assert out == {"a": 1}

    def test_empty_dict(self):
        enforcer = PermissionEnforcer()
        assert enforcer.filter_sensitive({}) == {}
