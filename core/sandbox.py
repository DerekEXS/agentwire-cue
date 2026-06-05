"""AgentWire-Cue v1.3.1+ persist path sandboxing.

v1.3.1 §3.4.2 — defense in depth (L1-L4).
v1.3.1 patch 2 §3.4.2 续 — 5 surfaces + per-plugin-name constraint.

Layers:
- L1: default allowed parents (整目录 ~/.local/share/agentwire-cue/ + /var/lib/agentwire-cue/)
- L2: spec.persist.allowed_parents_extras
- L3: --persist-allow-parent=PATH CLI flag
- L4: blocked parents (deny-list, NEVER overridable)

Surfaces (5):
- persist: state/<plugin.name>.json
- peer:   peers/<peer_id>.json
- log:    logs/<plugin.name>.log
- snapshot: snapshots/<plugin.name>-<ts>.json
- backups: state/<plugin.name>.corrupt (corrupt 备份跟 state 一起)

Per-surface rules (v1.3.1 patch 2 §3.4.2 续):
- 整目录 L1 allow (state/peers/logs/snapshots 子目录都 OK)
- 但 filename 必须以 plugin.name / peer_id 开头, 防止 plugin 互踩

Escape defense:
- normpath (../)
- realpath (symlink)
"""
from __future__ import annotations

import os
from pathlib import Path


# Default allowed parents (L1). 整目录 — 所有 5 surface 子目录都 OK.
_DEFAULT_ALLOWED_PARENTS: tuple[str, ...] = (
    "~/.local/share/agentwire-cue",  # 整目录 (state/ peers/ logs/ snapshots/ 都自然允许)
    "/var/lib/agentwire-cue",
)


# Blocked parents (L4, deny-list). NEVER overridable. v1.3.1 patch 1 §3.4.2.
_BLOCKED_PARENTS: tuple[str, ...] = (
    "~/.ssh", "~/.aws", "~/.gnupg", "~/.kube", "~/.docker",
    "/etc", "/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly",
    "/etc/cron.weekly", "/etc/cron.monthly",
    "/proc", "/sys", "/dev",
    "~/.bashrc", "~/.bash_profile", "~/.zshrc", "~/.zprofile", "~/.profile",
    "~/.netrc", "~/.pgpass", "~/.my.cnf",
)


# Valid surfaces (5)
SURFACE_PERSIST = "persist"
SURFACE_PEER = "peer"
SURFACE_LOG = "log"
SURFACE_SNAPSHOT = "snapshot"
SURFACE_BACKUPS = "backups"
VALID_SURFACES = frozenset({SURFACE_PERSIST, SURFACE_PEER, SURFACE_LOG, SURFACE_SNAPSHOT, SURFACE_BACKUPS})


# Surface parent directories (within L1 整目录)
_SURFACE_PARENT: dict[str, str] = {
    SURFACE_PERSIST: "state",
    SURFACE_PEER: "peers",
    SURFACE_LOG: "logs",
    SURFACE_SNAPSHOT: "snapshots",
    # SURFACE_BACKUPS 复用 state/ (跟 corrupt 备份一起, 避免第 5 个子目录)
    SURFACE_BACKUPS: "state",
}


# Surface filename patterns (per-plugin-name 约束)
_SURFACE_PATTERN: dict[str, str] = {
    SURFACE_PERSIST: "{plugin_name}.json",
    SURFACE_PEER: "{peer_id}.json",
    SURFACE_LOG: "{plugin_name}.log",
    SURFACE_SNAPSHOT: "{plugin_name}-*.json",
    SURFACE_BACKUPS: "{plugin_name}.corrupt",
}


class SandboxError(Exception):
    """Raised when a path violates sandbox rules. CLI shows this verbatim."""


def _abs(p: str | Path) -> Path:
    """Absolute + normpath (collapses .. and .). Does NOT follow symlinks."""
    s = os.path.expanduser(str(p))
    if not os.path.isabs(s):
        s = os.path.abspath(s)
    return Path(os.path.normpath(s))


def _real(p: str | Path) -> Path:
    """realpath — also follows symlinks. Catches symlink-based escape."""
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def _is_under(child: Path, parent: Path) -> bool:
    """True if child is parent or strictly under parent."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _build_allowed(
    spec_extras: list[str] | None,
    cli_extras: list[str] | None,
) -> list[Path]:
    """Compose L1 + L2 + L3 allowed parents."""
    out: list[Path] = []
    for p in _DEFAULT_ALLOWED_PARENTS:
        out.append(_abs(p))
    for p in spec_extras or []:
        out.append(_abs(p))
    for p in cli_extras or []:
        out.append(_abs(p))
    return out


def _check_surface_constraint(
    path: Path,
    surface: str,
    *,
    plugin_name: str | None = None,
    peer_id: str | None = None,
) -> None:
    """v1.3.1 patch 2 D6: per-surface filename + parent directory check.

    Surface=persist:  path must be <plugin_name>.json under state/
    Surface=peer:    path must be <peer_id>.json under peers/
    Surface=log:     path must be <plugin_name>.log under logs/
    Surface=snapshot: path must be <plugin_name>-*.json under snapshots/
    Surface=backups: path must be <plugin_name>.corrupt under state/
    """
    if surface not in VALID_SURFACES:
        raise SandboxError(
            f"SANDBOX_SURFACE_INVALID: unknown surface {surface!r}. "
            f"Valid: {sorted(VALID_SURFACES)}"
        )

    expected_parent_name = _SURFACE_PARENT[surface]
    if path.parent.name != expected_parent_name:
        raise SandboxError(
            f"SANDBOX_SURFACE_VIOLATION: surface={surface!r} requires file under "
            f"~/.local/share/agentwire-cue/{expected_parent_name}/, got {path}. "
            f"Fix: use the canonical surface path."
        )

    # Surface-specific extension + name constraint
    if surface == SURFACE_PERSIST:
        if not plugin_name:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: surface=persist requires plugin_name."
            )
        if path.suffix != ".json" or path.stem != plugin_name:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: persist file must be {plugin_name}.json, "
                f"got {path.name}."
            )
    elif surface == SURFACE_PEER:
        if not peer_id:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: surface=peer requires peer_id."
            )
        if path.suffix != ".json" or path.stem != peer_id:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: peer card must be {peer_id}.json, "
                f"got {path.name}."
            )
    elif surface == SURFACE_LOG:
        if not plugin_name:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: surface=log requires plugin_name."
            )
        if path.suffix != ".log" or path.stem != plugin_name:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: log file must be {plugin_name}.log, "
                f"got {path.name}."
            )
    elif surface == SURFACE_SNAPSHOT:
        if not plugin_name:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: surface=snapshot requires plugin_name."
            )
        if path.suffix != ".json" or not path.stem.startswith(f"{plugin_name}-"):
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: snapshot file must be "
                f"{plugin_name}-<ts>.json, got {path.name}."
            )
    elif surface == SURFACE_BACKUPS:
        if not plugin_name:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: surface=backups requires plugin_name."
            )
        if path.suffix != ".corrupt" or path.stem != plugin_name:
            raise SandboxError(
                f"SANDBOX_SURFACE_VIOLATION: backup file must be "
                f"{plugin_name}.corrupt, got {path.name}."
            )


def check_persist_path(
    path: str | Path,
    *,
    spec_extras: list[str] | None = None,
    cli_extras: list[str] | None = None,
) -> None:
    """v1.3.1 patch 1 backwards-compat: L1-L4 path-level sandbox.

    Surface-aware check should use check_surface_path() instead — this
    function does NOT enforce per-plugin-name filename constraint.
    """
    norm = _abs(path)
    real = _real(path)

    # L4: blocked
    for blocked in _BLOCKED_PARENTS:
        bp = _abs(blocked)
        if _is_under(norm, bp) or _is_under(real, bp):
            raise SandboxError(
                f"PERSIST_PATH_BLOCKED: {path} is under blocked parent {bp}. "
                f"Blocked parents are deny-listed and CANNOT be overridden by "
                f"--persist-allow-parent or spec.persist.allowed_parents_extras."
            )

    # L1+L2+L3
    allowed = _build_allowed(spec_extras, cli_extras)
    for parent in allowed:
        if _is_under(norm, parent) or _is_under(real, parent):
            return

    raise SandboxError(
        f"PERSIST_PATH_NOT_ALLOWED: {path} not under any allowed parent. "
        f"Default allowed: {[str(p) for p in _DEFAULT_ALLOWED_PARENTS]}. "
        f"Fix: use a path under one of the above, OR add "
        f"spec.persist.allowed_parents_extras: ['<dir>'] to plugin.yaml, "
        f"OR pass --persist-allow-parent=<dir> to cue host."
    )


def check_surface_path(
    path: str | Path,
    *,
    surface: str,
    plugin_name: str | None = None,
    peer_id: str | None = None,
    spec_extras: list[str] | None = None,
    cli_extras: list[str] | None = None,
) -> None:
    """v1.3.1 patch 2 D6: full surface-aware check.

    Combines L1-L4 (path is under allowed parent) with per-surface constraint
    (file is named correctly for the surface).
    """
    # Validate surface FIRST so callers get actionable error even if path is invalid
    if surface not in VALID_SURFACES:
        raise SandboxError(
            f"SANDBOX_SURFACE_INVALID: unknown surface {surface!r}. "
            f"Valid: {sorted(VALID_SURFACES)}"
        )
    # L1-L4 (path-level sandbox)
    check_persist_path(path, spec_extras=spec_extras, cli_extras=cli_extras)
    # Surface-level constraint
    norm = _abs(path)
    _check_surface_constraint(
        norm, surface,
        plugin_name=plugin_name, peer_id=peer_id,
    )


def is_persist_path_allowed(path: str | Path, **kwargs) -> bool:
    try:
        check_persist_path(path, **kwargs)
        return True
    except SandboxError:
        return False


def check_filesystem_path(path: str | Path, **kwargs) -> None:
    """L3 (runtime): same sandbox applies to ANY filesystem write, not just persist.

    Used by permission enforcer's check_filesystem to enforce the sandbox on
    the write_file action even when the plugin declares `filesystem: [{path: ...}]`
    rules that would otherwise allow arbitrary paths.
    """
    check_persist_path(path, **kwargs)


def get_default_allowed_parents() -> tuple[str, ...]:
    return _DEFAULT_ALLOWED_PARENTS


def get_blocked_parents() -> tuple[str, ...]:
    return _BLOCKED_PARENTS


def get_valid_surfaces() -> frozenset[str]:
    return VALID_SURFACES
