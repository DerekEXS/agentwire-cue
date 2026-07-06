"""AgentWire-Cue v1.3 loader.

Implements v1.3 §3 spec:
- YAML 1.2 parse via ruamel.yaml (NOT PyYAML — `on:` is bool-true under YAML 1.1)
- JSON Schema validation against v1.2.1 schema (caches schema in memory)
- Persist path resolve (only {{meta.*}} + literals, fail-fast on unknown vars)
- Writable check on persist path parent
- Secrets env var check (required=true → fail-fast on missing)
- 5-category permission field registration (PR3 fills the enforcer)
- Construct Plugin dataclass ready to run
- Error handling: per-plugin fail-fast, log + skip, never raise
- Directory discovery: recursive, *.yaml + *.yml, no symlink following
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import jsonschema
import ruamel.yaml

from .expression import ExpressionError, parse as parse_expression
from .sandbox import SandboxError, check_persist_path
from .types import Plugin, Trigger

log = logging.getLogger("agentwire_cue.loader")

# Schema path: bundled with package, NOT user-overridable (per spec §3.3 cache)
_SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "plugin.schema.json"
_SCHEMA_CACHE: dict | None = None


class LoaderError(Exception):
    """Raised internally; caught and logged at the public API."""


def _load_schema() -> dict:
    """Load and cache v1.2.1 plugin schema (spec §3.3 MUST cache)."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE


def load_yaml(path: Path) -> dict:
    """v1.3 §3.2: ruamel.yaml safe loader, YAML 1.2 (NOT PyYAML safe_load)."""
    yaml_parser = ruamel.yaml.YAML(typ="safe", pure=True)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml_parser.load(f)
    except FileNotFoundError as e:
        raise LoaderError(f"plugin file not found: {path}") from e
    except PermissionError as e:
        raise LoaderError(f"plugin file not readable: {path}") from e
    except UnicodeDecodeError as e:
        raise LoaderError(f"plugin file encoding error: {path} ({e})") from e
    except ruamel.yaml.YAMLError as e:
        # ruamel exposes line/column via problem_mark
        mark = getattr(e, "problem_mark", None)
        loc = f"line {mark.line + 1}:col {mark.column + 1}" if mark else "unknown"
        raise LoaderError(f"YAML parse error in {path} at {loc}: {e}") from e
    if not isinstance(data, dict):
        raise LoaderError(f"plugin file {path} is not a YAML mapping (got {type(data).__name__})")
    return data


def validate_schema(plugin_dict: dict, *, path: Path) -> None:
    """v1.3 §3.3: jsonschema validate. Aggregates ALL errors, raises once."""
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(plugin_dict), key=lambda e: list(e.path))
    if not errors:
        return
    msgs: list[str] = []
    for err in errors:
        ptr = "/".join(str(p) for p in err.absolute_path) or "<root>"
        msgs.append(f"[{ptr}] {err.validator}: {err.message} (value={err.instance!r})")
    raise LoaderError(
        f"plugin {path.name} schema validation failed ({len(errors)} errors):\n"
        + "\n".join(f"  - {m}" for m in msgs)
    )


def resolve_persist_path(
    template: str | None,
    meta: dict,
    *,
    spec_extras: list[str] | None = None,
    cli_extras: list[str] | None = None,
) -> Path | None:
    """v1.3 §3.4: only {{meta.*}} + literals allowed. Fail-fast on others.

    v1.3.1 §3.4.2: also enforce path sandbox (L1 default + L2 spec + L3 CLI
    allowed parents; L4 blocked deny-list always applies).
    """
    if not template:
        return None
    resolved = template
    for key, value in meta.items():
        resolved = resolved.replace("{{meta." + key + "}}", str(value))
    if "{{" in resolved:
        raise LoaderError(
            f"persist.path template has non-meta variables: {template!r}"
        )
    path = Path(resolved).expanduser()
    try:
        check_persist_path(path, spec_extras=spec_extras, cli_extras=cli_extras)
    except SandboxError as e:
        raise LoaderError(str(e)) from e
    return path


def check_persist_writable(path: Path) -> None:
    """v1.3 §3.4.1: parent mkdir + touch+unlink smoke test. Fail-fast on error."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        raise LoaderError(f"persist.path parent not creatable: {path} ({e})")
    test_file = path.with_suffix(path.suffix + ".test")
    try:
        test_file.touch()
        test_file.unlink()
    except (OSError, PermissionError) as e:
        raise LoaderError(f"persist.path not writable: {path} ({e})")


def check_secrets(secrets: list[dict]) -> dict[str, str]:
    """v1.3 §3.5: required=true env vars MUST be set at startup."""
    resolved: dict[str, str] = {}
    for secret in secrets or []:
        name = secret["name"]
        required = bool(secret.get("required", False))
        value = os.environ.get(name)
        if value is None:
            if required:
                raise LoaderError(f"required secret env var not set: {name}")
            continue
        resolved[name] = value
    return resolved


def _deep_merge(base: dict, overlay: dict) -> dict:
    """v1.6.5: recursive dict merge with list replacement.

    - Scalar leaves in ``overlay`` win over ``base``.
    - Nested dicts recurse.
    - Lists replace atomically (no item-by-item merge) so e.g.
      ``permissions.peers`` is never partially merged.

    Mutates neither ``base`` nor ``overlay`` — returns a new dict.
    """
    result = dict(base)
    for key, overlay_val in overlay.items():
        base_val = result.get(key)
        if isinstance(base_val, dict) and isinstance(overlay_val, dict):
            result[key] = _deep_merge(base_val, overlay_val)
        else:
            result[key] = overlay_val
    return result


def _apply_local_overlay(plugin_path: Path, plugin_dict: dict) -> dict:
    """v1.6.5: merge ``production.local.yaml`` over ``cue.yaml`` if present.

    Convention: each shipped example plugin (``examples/<name>/cue.yaml``)
    may have a sibling ``production.local.yaml`` with real peer identifiers,
    URLs, and token paths. The overlay is gitignored so operators can store
    real values without committing them. The merged result is validated
    against the same v1.2 schema as the base file — invalid keys fail at
    load time.
    """
    overlay_path = plugin_path.parent / "production.local.yaml"
    if not overlay_path.exists() or overlay_path.is_symlink():
        return plugin_dict
    try:
        overlay = load_yaml(overlay_path)
    except LoaderError as e:
        log.warning(
            "production.local.yaml overlay load failed for %s: %s (using base cue.yaml)",
            plugin_path.name, e,
        )
        return plugin_dict
    log.info("applying production.local.yaml overlay for %s", plugin_path.name)
    return _deep_merge(plugin_dict, overlay)


def get_safe_env(plugin_secrets: dict[str, str]) -> dict[str, str]:
    """v1.3 §3.5.1: action subprocess env MUST be explicit (best-effort isolation).

    Caller is required to pass `env=get_safe_env(...)` to subprocess.run.
    MUST NOT pass `env=None` or `env=dict(os.environ, ...)` — see §3.5.1.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        **plugin_secrets,
    }


def _validate_guards(plugin_dict: dict, *, path: Path) -> list[str]:
    """v1.3 §5.10: parse all guard expressions at startup; aggregate errors."""
    errors: list[str] = []
    states = plugin_dict.get("spec", {}).get("statechart", {}).get("states", {})
    for state_name, state_def in states.items():
        on_handlers = state_def.get("on", {})
        if not isinstance(on_handlers, dict):
            continue
        for event_name, transitions in on_handlers.items():
            if not isinstance(transitions, list):
                transitions = [transitions]
            for t in transitions:
                if not isinstance(t, dict):
                    continue
                guard = t.get("guard")
                if guard is None:
                    continue
                try:
                    parse_expression(guard)
                except ExpressionError as e:
                    errors.append(
                        f"guard parse error in state {state_name!r} on event {event_name!r}: {e}"
                    )
    return errors


def _validate_targets(plugin_dict: dict, *, path: Path) -> list[str]:
    """v1.3.1 §4.7.1 P0-2: every transition target MUST be a declared state.

    Catches `target: nonexistent` style errors at load time, before any
    transition can put the engine in an undefined state.
    """
    errors: list[str] = []
    states = plugin_dict.get("spec", {}).get("statechart", {}).get("states", {}) or {}
    state_ids = set(states.keys())
    # v1.5.9: also validate resilience.on_exhaust target (previously unchecked;
    # a spelling mistake in on_exhaust would silently fail at runtime).
    resilience = plugin_dict.get("spec", {}).get("resilience") or {}
    on_exhaust = (resilience.get("on_exhaust") if isinstance(resilience, dict) else None)
    if on_exhaust and on_exhaust not in state_ids:
        available = sorted(state_ids)
        errors.append(
            f"ON_EXHAUST_NOT_IN_STATES: resilience.on_exhaust "
            f"{on_exhaust!r} not in spec.states {available}. Fix plugin.yaml."
        )
    for state_name, state_def in states.items():
        if not isinstance(state_def, dict):
            continue
        on_handlers = state_def.get("on", {})
        if not isinstance(on_handlers, dict):
            continue
        for event_name, transitions in on_handlers.items():
            if not isinstance(transitions, list):
                transitions = [transitions]
            for t in transitions:
                if not isinstance(t, dict):
                    continue
                target = t.get("target")
                if target is None:
                    continue
                if target not in state_ids:
                    available = sorted(state_ids)
                    errors.append(
                        f"TARGET_NOT_IN_STATES: state {state_name!r}.on.{event_name}.target "
                        f"{target!r} not in spec.states {available}. Fix plugin.yaml."
                    )
    return errors


def _extract_triggers(spec: dict) -> list[Trigger]:
    raw = spec.get("triggers", [])
    return [Trigger(id=t["id"], type=t["type"], config=t.get("config", {})) for t in raw]


def load_plugin(plugin_path: Path) -> Plugin | None:
    """Public entry. Returns Plugin or None on error (logs, doesn't raise).

    v1.3 §3.7.1: MUST NOT raise; MUST log ERROR with path + details.
    """
    try:
        plugin_dict = load_yaml(plugin_path)
        # v1.6.5: apply production.local.yaml overlay (if present) before
        # schema validation so the merged shape is checked, not just the base.
        plugin_dict = _apply_local_overlay(plugin_path, plugin_dict)
        validate_schema(plugin_dict, path=plugin_path)

        meta = plugin_dict.get("metadata", {})
        name = meta.get("name", plugin_path.stem)
        version = meta.get("version", "0.0.0")
        api_version = plugin_dict.get("apiVersion", "")

        spec = plugin_dict.get("spec", {})

        # Persist path resolution + sandbox + writable check
        persist_cfg = spec.get("statechart", {}).get("persist") or {}
        path_template = persist_cfg.get("path")
        spec_extras = persist_cfg.get("allowed_parents_extras") or []
        cli_extras = []  # set by host CLI in v1.4 (P1-3)
        resolved_path = resolve_persist_path(
            path_template, meta,
            spec_extras=spec_extras, cli_extras=cli_extras,
        )
        if resolved_path is not None:
            check_persist_writable(resolved_path)

        # Secrets env check
        secrets = check_secrets(spec.get("secrets", []))

        # Guard expression parse check (spec §5.10 fail-fast)
        guard_errors = _validate_guards(plugin_dict, path=plugin_path)
        if guard_errors:
            raise LoaderError(
                f"plugin {name} guard validation failed ({len(guard_errors)} errors):\n"
                + "\n".join(f"  - {e}" for e in guard_errors)
            )

        # v1.3.1 P0-2: target validation (loader-time fail-fast)
        target_errors = _validate_targets(plugin_dict, path=plugin_path)
        if target_errors:
            raise LoaderError(
                f"plugin {name} target validation failed ({len(target_errors)} errors):\n"
                + "\n".join(f"  - {e}" for e in target_errors)
            )

        # Permission registration — 5 categories (PR3 fleshes out enforcer)
        permissions = spec.get("permissions", {})

        # v1.4.8: peer alias table (optional)
        peers = spec.get("peers", {}) or {}

        # v1.5.2: cross-plugin dependency block (optional)
        requires = spec.get("requires", {}) or {}

        triggers = _extract_triggers(spec)

        log.info(
            "loaded plugin %s v%s (apiVersion=%s, triggers=%d, persist=%s, peers=%d)",
            name, version, api_version, len(triggers),
            str(resolved_path) if resolved_path else "(none)",
            len(peers),
        )

        return Plugin(
            name=name,
            version=version,
            api_version=api_version,
            meta=meta,
            spec=spec,
            resolved_persist_path=resolved_path,
            permissions=permissions,
            secrets=secrets,
            triggers=triggers,
            source_path=plugin_path,
            peers=peers,
            requires=requires,
        )
    except LoaderError as e:
        log.error("failed to load plugin %s: %s", plugin_path, e)
        return None
    except Exception as e:  # last-resort safety net
        log.exception("unexpected error loading plugin %s: %s", plugin_path, e)
        return None


def discover_plugins(plugin_dir: Path) -> list[Path]:
    """v1.3 §3.8: recursive, *.yaml + *.yml, no symlink following."""
    if not plugin_dir.exists():
        log.warning("plugin dir does not exist: %s", plugin_dir)
        return []
    found: list[Path] = []
    for pattern in ("*.yaml", "*.yml"):
        for p in sorted(plugin_dir.rglob(pattern)):
            # rglob follows symlinks by default; explicitly skip symlinks
            if p.is_symlink():
                log.warning("skipping symlink: %s", p)
                continue
            # v1.6.5: *.local.yaml files are overlays (production.local.yaml
            # or <peer>.local.yaml), not standalone plugins. Skip them to
            # avoid spurious schema-validation failures; the loader applies
            # them automatically as siblings of cue.yaml.
            if p.name.endswith(".local.yaml") or p.name.endswith(".local.yml"):
                continue
            found.append(p)
    return found


def load_all(plugin_dir: Path) -> list[Plugin]:
    """Load all plugins in dir. Per-plugin errors are logged + skipped."""
    paths = discover_plugins(plugin_dir)
    plugins: list[Plugin] = []
    for p in paths:
        plugin = load_plugin(p)
        if plugin is not None:
            plugins.append(plugin)
    # v1.3 §2.6: 0 plugins loaded → exit 1 (the host's responsibility, but
    # the loader surfaces the condition here)
    if paths and not plugins:
        log.error("0 plugins successfully loaded from %s", plugin_dir)
    elif not paths:
        log.warning("no plugin files (*.yaml/*.yml) found under %s", plugin_dir)
    return plugins
