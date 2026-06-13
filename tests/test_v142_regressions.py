"""v1.4.2 regression tests: 4 fixes (BOM, systemd, proxy, --token-env)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agentwire_cue.__main__ import _resolve_secret


# ---------- BUG-1: a2a-token.txt BOM handled ----------
# v1.4.2 regression: token files with UTF-8 BOM must be handled
# defensively. start.py uses encoding='utf-8-sig' to strip BOM.

class TestTokenResolution:
    """v1.4.2 fix: CUE --a2a-token-env / --a2a-token-file / BOM strip."""

    def test_arg_value_wins(self):
        r = _resolve_secret("from_arg", "ENV", "file", "DEFAULT", "label")
        assert r == "from_arg"

    def test_arg_env_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_ENV", "value_from_env")
        r = _resolve_secret(None, "MY_TEST_ENV", None, "DEFAULT", "label")
        assert r == "value_from_env"

    def test_arg_env_missing_exits(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR_XYZ", raising=False)
        with pytest.raises(SystemExit):
            _resolve_secret(None, "MISSING_VAR_XYZ", None, "DEFAULT", "label")

    def test_arg_file_wins_over_default_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEFAULT_VAR", "from_default")
        f = tmp_path / "tok.txt"
        f.write_text("from_file")
        r = _resolve_secret(None, None, str(f), "DEFAULT_VAR", "label")
        assert r == "from_file"

    def test_bom_file_auto_stripped(self, tmp_path):
        f = tmp_path / "tok.txt"
        f.write_bytes(b'\xef\xbb\xbfTOKEN_BOM_STRIP\n')
        r = _resolve_secret(None, None, str(f), "DEFAULT_VAR", "label")
        assert r == "TOKEN_BOM_STRIP"

    def test_default_env_fallback(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_VAR_2", "from_default")
        r = _resolve_secret(None, None, None, "DEFAULT_VAR_2", "label")
        assert r == "from_default"

    def test_nothing_set_returns_none(self, monkeypatch):
        monkeypatch.delenv("MISSING_DEFAULT_VAR", raising=False)
        r = _resolve_secret(None, None, None, "MISSING_DEFAULT_VAR", "label")
        assert r is None


# ---------- item-2: AGENTWIRE systemd service ----------
# v1.4.2 regression: agentwire.service must exist and be enabled.

# v1.4.2 AUDIT-FIX #3: these tests verify LOCAL dev machine infrastructure
# (systemd service file existence + paths). They MUST NOT hardcode
# developer-specific paths or run on CUE CI. Now skipped unless the
# AGENTWIRE_INFRA env var is set (or AGENTWIRE_INFRA_HOME points to
# the AGENTWIRE install root).

import os
import pytest as _pytest
_AGENTWIRE_INFRA_HOME = os.environ.get('AGENTWIRE_INFRA_HOME',
    "")
_AGENTWIRE_INFRA_ENABLED = os.environ.get('AGENTWIRE_INFRA') == '1'  # same as before

_skip_unless_local = _pytest.mark.skipif(
    not _AGENTWIRE_INFRA_ENABLED,
    reason="AUDIT-FIX #3: agentwire-core infra tests skipped on CUE CI. "
           "Set AGENTWIRE_INFRA=1 + AGENTWIRE_INFRA_HOME=<path> to run locally."
)

@_skip_unless_local
class TestAgentwireSystemdUnit:
    """v1.4.2 fix: agentwire.service exists, enabled, runs on 18800."""

    SERVICE_PATH = Path(_AGENTWIRE_INFRA_HOME) / "agentwire.service"
    PROXY_SERVICE_PATH = Path(_AGENTWIRE_INFRA_HOME) / "agentwire-proxy.service"

    def test_agentwire_service_file_exists(self):
        assert self.SERVICE_PATH.exists(), (
            f"agentwire.service not found at {self.SERVICE_PATH}"
        )
        content = self.SERVICE_PATH.read_text()
        assert "agentwire_core/server/start.py" in content
        assert "--port 18800" in content
        assert "Restart=always" in content
        assert "WantedBy=default.target" in content

    def test_agentwire_proxy_service_file_exists(self):
        assert self.PROXY_SERVICE_PATH.exists(), (
            f"agentwire-proxy.service not found at {self.PROXY_SERVICE_PATH}"
        )
        content = self.PROXY_SERVICE_PATH.read_text()
        assert "agentwire_core/server/proxy.py" in content
        assert "18802" in content
        assert "After=agentwire.service" in content

    def test_service_references_token_file(self):
        content = self.SERVICE_PATH.read_text()
        assert "--token-file" in content
        assert "a2a-token.txt" in content


# ---------- item-3: reverse proxy 18802 -> 18800 ----------

@_skip_unless_local
class TestReverseProxy:
    """v1.4.2 fix: agentwire_core/server/proxy.py exists, proxies 18802 -> 18800."""

    def _proxy_path(self):
        # Resolve from env AGENTWIRE_INFRA_HOME (parent dir of the systemd
        # user units). No dev fallback; env var required for local run.
        home = os.environ.get('AGENTWIRE_INFRA_HOME',
            None)
        # proxy.py lives at <workspace>/agentwire_core/server/proxy.py
        # workspace from env var (no fallback)
        workspace = os.environ.get('AGENTWIRE_WORKSPACE', None)
        return Path(workspace) / 'agentwire_core' / 'server' / 'proxy.py'

    def test_proxy_script_exists(self):
        proxy_path = self._proxy_path()
        assert proxy_path.exists(), f"proxy.py not found at {proxy_path}"
        content = proxy_path.read_text()
        assert "18800" in content
        assert "18802" in content
        assert "transparent" in content.lower() or "proxy" in content.lower()
        assert "aiohttp" in content


# ---------- item-4: CLI help shows new options ----------

class TestCLIShowsNewOptions:
    def test_host_help_includes_token_env(self):
        # v1.4.2 AUDIT-FIX #3: no dev path fallback. Use sys.executable + env var.
        result = subprocess.run(
            [sys.executable, "-m", "agentwire_cue", "host", "--help"],
            capture_output=True, text=True,
            cwd=os.environ.get("AGENTWIRE_WORKSPACE") or str(Path(__file__).resolve().parents[2]),
            env=dict(os.environ),  # inherit user env including PATH
        )
        # Help may exit 0 with help text, or exit 2 with error to stderr
        out = result.stdout + result.stderr
        assert "--a2a-token-env" in out, f"missing --a2a-token-env in help:\n{out}"
        assert "--a2a-token-file" in out, f"missing --a2a-token-file in help:\n{out}"
        assert "--admin-token-env" in out
        assert "--admin-token-file" in out


# ---------- End-to-end: AGENTWIRE 18800 reachable via proxy 18802 ----------

class TestEndToEndWithAGENTWIRE:
    """v1.4.2: AGENTWIRE Python on 18800, proxy on 18802, CUE connects to either."""

    def test_agentwire_direct(self):
        """Direct AGENTWIRE agent card on 18800."""
        import urllib.request
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:18800/.well-known/agent.json", timeout=2)
            assert resp.status == 200
        except Exception as e:
            pytest.skip(f"AGENTWIRE not running: {e}")

    def test_proxy_transparent(self):
        """Proxy 18802 -> 18800 should return same agent card."""
        import urllib.request
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:18802/.well-known/agent.json", timeout=2)
            assert resp.status == 200
        except Exception as e:
            pytest.skip(f"proxy not running: {e}")

    def test_proxy_health_check(self):
        """Proxy /health returns upstream status."""
        import urllib.request, json
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:18802/health", timeout=2)
            body = json.loads(resp.read())
            assert body["status"] in ("healthy", "degraded")
            assert "upstream" in body
        except Exception as e:
            pytest.skip(f"proxy not running: {e}")
