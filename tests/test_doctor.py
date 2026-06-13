"""v1.5.2 RED tests for ``agentwire-cue doctor`` checks.

The doctor surfaces deployment problems that today only show up at
runtime: BOM-corrupted tokens, unreachable CORE, port conflicts, leaked
proxy env vars, broken plugin dependencies.

Contract:
- Each check is a callable that returns a ``DoctorResult`` with
  ``status`` in ``{"ok", "warn", "fail"}`` and a short ``message``.
- Checks never raise: a failure becomes a ``"fail"`` result with the
  cause in the message.
- The CLI entry point composes checks and prints one line per check
  prefixed by ``OK`` / ``WARN`` / ``FAIL`` (the icons are added by the
  formatter; tests assert against the prefix tokens).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentwire_cue.core import doctor


# ---------- token health ----------


def test_check_token_file_ok(tmp_path):
    f = tmp_path / "token.txt"
    f.write_text("plain-token-value\n", encoding="utf-8")
    result = doctor.check_token_file(f)
    assert result.status == "ok"


def test_check_token_file_detects_bom(tmp_path):
    f = tmp_path / "token.txt"
    f.write_bytes(b"\xef\xbb\xbfsecret-value\n")
    result = doctor.check_token_file(f)
    assert result.status == "warn"
    assert "BOM" in result.message


def test_check_token_file_detects_crlf(tmp_path):
    f = tmp_path / "token.txt"
    f.write_bytes(b"secret-value\r\n")
    result = doctor.check_token_file(f)
    assert result.status == "warn"
    assert "CRLF" in result.message


def test_check_token_file_missing(tmp_path):
    f = tmp_path / "absent.txt"
    result = doctor.check_token_file(f)
    assert result.status == "fail"
    assert "not found" in result.message.lower() or "does not exist" in result.message.lower()


# ---------- proxy ----------


def test_check_proxy_env_warns_when_set(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7897")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    result = doctor.check_proxy_env()
    assert result.status == "warn"
    assert "http_proxy" in result.message


def test_check_proxy_env_ok_when_unset(monkeypatch):
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    result = doctor.check_proxy_env()
    assert result.status == "ok"


# ---------- port ----------


def test_check_port_available_when_free():
    result = doctor.check_port_available(0)
    assert result.status == "ok"


def test_check_port_available_when_busy():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(doctor, "_port_owner_command", lambda busy_port: None)
        try:
            result = doctor.check_port_available(port, host="127.0.0.1")
        finally:
            monkeypatch.undo()
        assert result.status == "info"
        assert str(port) in result.message
    finally:
        s.close()



def test_check_port_reports_ok_when_owned_by_agentwire_cue(monkeypatch):
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        monkeypatch.setattr(
            doctor,
            "_port_owner_command",
            lambda busy_port: "python3 -m agentwire_cue host --plugin-dir /plugins" if busy_port == port else None,
            raising=False,
        )
        result = doctor.check_port_available(port, host="127.0.0.1")
        assert result.status == "ok"
        assert "agentwire_cue" in result.message
    finally:
        s.close()



def _write_plugin(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(body, encoding="utf-8")
    return p


_MINIMAL_TRIGGER = (
    "  triggers:\n"
    "    - id: t-tick\n"
    "      type: cron\n"
    "      config:\n"
    "        expression: \"0 0 * * *\"\n"
    "        timezone: \"UTC\"\n"
)
_MINIMAL_TAIL = (
    "  statechart:\n"
    "    initial: s0\n"
    "    states:\n"
    "      s0: {}\n"
    "  secrets: []\n"
    "  permissions:\n"
    "    network: { http_egress: [], raw_socket: false }\n"
    "    filesystem: []\n"
    "    subprocess: { allow: [] }\n"
    "    env: []\n"
    "    peers: []\n"
    "    timers: { max_concurrent: 1, min_interval_ms: 1000 }\n"
)


def test_check_plugin_dependencies_ok(tmp_path):
    body = (
        "apiVersion: agentwire/v1.2\n"
        "kind: plugin\n"
        "metadata:\n"
        "  name: solo\n"
        "  version: 1.0.0\n"
        "spec:\n"
        + _MINIMAL_TRIGGER
        + _MINIMAL_TAIL
    )
    _write_plugin(tmp_path, "solo", body)
    result = doctor.check_plugin_dependencies(tmp_path)
    assert result.status == "ok"


def test_check_plugin_dependencies_fail(tmp_path):
    body = (
        "apiVersion: agentwire/v1.2\n"
        "kind: plugin\n"
        "metadata:\n"
        "  name: needs-ghost\n"
        "  version: 1.0.0\n"
        "spec:\n"
        "  requires:\n"
        "    plugins: [does-not-exist]\n"
        + _MINIMAL_TRIGGER
        + _MINIMAL_TAIL
    )
    _write_plugin(tmp_path, "needs-ghost", body)
    result = doctor.check_plugin_dependencies(tmp_path)
    assert result.status == "fail"
    assert "needs-ghost" in result.message


# ---------- CLI integration ----------


def test_cli_doctor_prints_check_lines(capsys, tmp_path, monkeypatch):
    # Force a busy port so doctor surfaces at least one WARN line.
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("all_proxy", raising=False)
        from agentwire_cue.__main__ import main as cli_main
        exit_code = cli_main([
            "doctor",
            "--a2a-listener-port", str(port),
            "--admin-port", "0",
            "--no-network",
        ])
    finally:
        s.close()
    out = capsys.readouterr().out
    assert "AgentWire CUE Doctor" in out
    assert "Port" in out
    assert "Proxy" in out
    assert exit_code in (0, 1)  # Doctor never raises; informational
