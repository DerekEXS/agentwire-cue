"""Redaction client for AgentWire-Cue v1.4.3.

Pulls the shared redaction pattern catalog from the AgentWire CORE
gateway's /redact/patterns endpoint, caches it locally for 24h, and
exposes a `redact(text)` function for use in cue plugins.

Cache location: ~/.cache/agentwire/redact_patterns.json
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


CACHE_DIR = Path.home() / ".cache" / "agentwire"
CACHE_FILE = CACHE_DIR / "redact_patterns.json"
CACHE_TTL_SECONDS = 86400  # 24h


def _load_cache() -> dict | None:
    try:
        if not CACHE_FILE.exists():
            return None
        mtime = CACHE_FILE.stat().st_mtime
        if time.time() - mtime > CACHE_TTL_SECONDS:
            return None
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _fetch_remote(a2a_url: str, token: str, timeout: int = 5) -> dict:
    url = a2a_url.rstrip("/") + "/redact/patterns"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class RedactClient:
    """Fetches /redact/patterns from CORE; caches for 24h.

    Falls back to local cache (24h) when the gateway is unreachable;
    if both fail, returns a minimal built-in fallback.
    """

    BUILTIN_FALLBACK = {
        "version": "1",
        "patterns": [
            {"name": "bearer_token", "regex": r"(?i)Bearer\s+[a-zA-Z0-9._\-]{20,}",
             "replacement": "Bearer [REDACTED:TOKEN]"},
            {"name": "anthropic_key", "regex": r"sk-ant-[a-zA-Z0-9\-_]{20,}",
             "replacement": "[REDACTED:ANTHROPIC_KEY]"},
        ],
    }

    def __init__(self, a2a_url: str, token: str, force_refresh: bool = False):
        self.a2a_url = a2a_url
        self.token = token
        self._patterns: list[tuple[str, "re.Pattern", str]] = []
        catalog = None
        if not force_refresh:
            catalog = _load_cache()
        if catalog is None:
            try:
                catalog = _fetch_remote(a2a_url, token)
                _save_cache(catalog)
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                # Use stale cache if present, else builtin fallback
                if CACHE_FILE.exists():
                    try:
                        catalog = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        catalog = self.BUILTIN_FALLBACK
                else:
                    catalog = self.BUILTIN_FALLBACK
        self._catalog = catalog
        for p in catalog.get("patterns", []):
            try:
                self._patterns.append((p["name"], re.compile(p["regex"]), p["replacement"]))
            except (re.error, KeyError):
                pass

    def redact(self, text: str) -> str:
        for _name, pattern, repl in self._patterns:
            text = pattern.sub(repl, text)
        return text

    def catalog_version(self) -> str:
        return self._catalog.get("version", "?")

    def pattern_count(self) -> int:
        return len(self._patterns)
