from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from agentwire_cue.__main__ import build_parser
from agentwire_cue.core import doctor as doctor_mod


def test_doctor_cli_defaults_a2a_url_to_loopback():
    parser = build_parser()

    args = parser.parse_args(["doctor", "--no-network"])

    assert args.a2a_url == "http://127.0.0.1:18800"


def test_doctor_picks_up_docker_compose_a2a_url_via_env(monkeypatch):
    monkeypatch.setenv("CUE_DOCTOR_A2A_URL", "http://agentwire-core:18800")
    parser = build_parser()
    args = parser.parse_args(["doctor", "--no-network"])
    assert args.a2a_url == "http://agentwire-core:18800"


def test_check_core_reachable_reports_info_when_container_dns_unresolved():
    """v1.5.7: in-container CORE URL is meaningless from the host shell;
    downgrade to INFO so the operator's doctor/healthcheck stays clean.
    """
    fake_probe = AsyncReturningTuple(False, "NameResolutionError: agentwire-core")
    with patch.object(doctor_mod, "_probe_url", fake_probe):
        result = doctor_mod.check_core_reachable("http://agentwire-core:18800")
    assert result.status == "info"
    assert "container DNS" in result.message


def test_check_core_reachable_reports_info_when_core_not_listening_on_loopback():
    fake_probe = AsyncReturningTuple(False, "ConnectionRefusedError: 127.0.0.1:18800")
    with patch.object(doctor_mod, "_probe_url", fake_probe):
        result = doctor_mod.check_core_reachable("http://127.0.0.1:18800")
    assert result.status == "info"
    assert "loopback" in result.message


class AsyncReturningTuple:
    def __init__(self, reachable, detail):
        self._reachable = reachable
        self._detail = detail

    async def __call__(self, *_args, **_kwargs):
        return (self._reachable, self._detail)
