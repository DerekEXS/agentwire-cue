"""Core types shared across loader / statechart / permission modules.

PR1 scope: just Plugin + Trigger + StateMetadata. Statechart and Permission
flesh out in PR2 and PR3 respectively.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Trigger:
    """A trigger definition from spec.triggers[].

    Concrete scheduling logic lives in core.scheduler (PR3).
    """
    id: str
    type: str  # "a2a_message_type" | "cron"
    config: dict


@dataclass
class StateMetadata:
    """Per v1.3 §4.2: id, entered_at_ms, duration_ms (computed, not stored)."""
    id: str
    entered_at_ms: int
    duration_ms: int = 0


@dataclass
class Plugin:
    """A loaded, validated, resolved plugin ready to run.

    PR1: statechart (StatechartEngine) and permission_enforcer may be None
    placeholders. PR2 / PR3 fill them in via setter injection.
    """
    name: str
    version: str
    api_version: str
    meta: dict
    spec: dict
    resolved_persist_path: Path | None
    statechart: Any = None  # StatechartEngine — PR2
    permissions: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)
    triggers: list[Trigger] = field(default_factory=list)
    source_path: Path | None = None
    # v1.4.8: peer alias table. Keyed by alias name (e.g. "Pawly"),
    # values are dicts containing at minimum {"uuid": "...", "url": "..."}.
    peers: dict = field(default_factory=dict)
    # v1.5.2: cross-plugin dependency block. Optional sub-keys are
    # ``plugins`` / ``peers`` / ``capabilities``. ``degraded`` flips to
    # True when ``Host._check_requires`` finds any missing dependency.
    requires: dict = field(default_factory=dict)
    degraded: bool = False
    degraded_reason: str | None = None
