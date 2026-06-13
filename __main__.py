"""AgentWire-Cue — Cue (v1.4) host implementation.

CLI entry: python -m agentwire_cue <subcommand> [args]

Subcommands:
- validate <file-or-dir>   Validate plugin yaml (schema + guard parse + YAML 1.1 lint)
- host [options]           Start the cue host (load plugins + a2a + admin)
- version                  Print host version
- help                     Show this help

Exit codes:
- 0  success
- 1  validation/load failed
- 2  internal error
- 130  SIGINT (Ctrl-C)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import signal
import sys
from pathlib import Path

from . import __version__
from .core.host import Host
from .core.loader import (
    LoaderError,
    discover_plugins,
    load_all,
    load_plugin,
    load_yaml,
    validate_schema,
)
from .core.expression import ExpressionError, parse as parse_expression

log = logging.getLogger("agentwire_cue.cli")


# ---------- YAML 1.1 lint ----------

_YAML11_BOOL_TOKENS = frozenset({"on", "off", "yes", "no", "y", "n", "true", "false"})


def _lint_yaml_11(path: Path, text: str) -> list[str]:
    warnings: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0]
        if not line.strip():
            continue
        m = re.match(r"^(\s*)([A-Za-z_][\w.\-]*)\s*:\s*(\S+)\s*$", line)
        if not m:
            continue
        indent, key, value = m.group(1), m.group(2), m.group(3)
        if key == "on" and not (raw.lstrip().startswith('"on"') or raw.lstrip().startswith("'on'")):
            warnings.append(
                f"{path}:{lineno}: unquoted `on:` key — YAML 1.1 will parse as bool True. "
                f'Use `"on":` (with quotes) to be safe.'
            )
    return warnings


# ---------- validate ----------

def cmd_validate(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return 1
    paths = [target] if target.is_file() else discover_plugins(target)
    if not paths:
        print(f"no plugin files (*.yaml/*.yml) found under {target}", file=sys.stderr)
        return 1
    failures = 0
    warnings_total = 0
    for p in paths:
        print(f"==> {p}")
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  read failed: {e}", file=sys.stderr)
            failures += 1
            continue
        warnings = _lint_yaml_11(p, text)
        for w in warnings:
            print(f"  warn: {w}")
        warnings_total += len(warnings)
        result = load_plugin(p)
        if result is None:
            print(f"  FAIL: {p.name} did not load (see log)", file=sys.stderr)
            failures += 1
        else:
            print(f"  ok:   {result.name} v{result.version} (apiVersion={result.api_version})")
            print(f"        triggers={len(result.triggers)}, persist={result.resolved_persist_path}")
    print()
    print(f"summary: {len(paths) - failures}/{len(paths)} ok, {warnings_total} lint warnings, {failures} failures")
    return 0 if failures == 0 else 1


# ---------- token resolution helpers ----------

def _read_token_file(path: str) -> str:
    """Read token from file, stripping UTF-8 BOM and whitespace."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError as e:
        print(f"error: cannot read token file {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _resolve_secret(
    arg_value: str | None,
    arg_env: str | None,
    arg_file: str | None,
    default_env: str,
    label: str,
) -> str | None:
    """Resolve a secret from 4 sources, in priority order:
    1. arg_value (CLI flag — visible in argv, but explicit)
    2. arg_env (env var name) — env var specified by user
    3. arg_file (file path) — file with BOM auto-stripped
    4. default_env (default env var) — fallback
    Returns None if none set.
    """
    if arg_value is not None:
        return arg_value
    if arg_env is not None:
        val = os.environ.get(arg_env)
        if val is None:
            print(f"error: --{label}-env={arg_env} specified but env not set", file=sys.stderr)
            sys.exit(1)
        return val
    if arg_file is not None:
        return _read_token_file(arg_file)
    return os.environ.get(default_env)


# ---------- host ----------

async def _run_host(args: argparse.Namespace) -> int:
    """v1.4 §2.1: 10-step startup + run + signal-driven shutdown."""
    plugin_dir = Path(args.plugin_dir)
    if not plugin_dir.exists():
        print(f"error: --plugin-dir {plugin_dir} does not exist", file=sys.stderr)
        return 1

    # Resolve a2a_token: --a2a-token > --a2a-token-env > --a2a-token-file > env AGENTWIRE_TOKEN
    a2a_token = _resolve_secret(
        args.a2a_token, args.a2a_token_env, args.a2a_token_file,
        default_env="AGENTWIRE_TOKEN", label="a2a-token",
    )
    # Resolve admin_token: --admin-token > --admin-token-env > --admin-token-file > env CUE_ADMIN_TOKEN
    admin_token = _resolve_secret(
        args.admin_token, args.admin_token_env, args.admin_token_file,
        default_env="CUE_ADMIN_TOKEN", label="admin-token",
    )

    host = Host(
        plugin_dir=plugin_dir,
        a2a_url=args.a2a_url,
        a2a_token=a2a_token,
        admin_token=admin_token,
        admin_port=args.admin_port,
        admin_host=args.admin_host,
        a2a_listener_port=args.a2a_listener_port,
        a2a_listener_host=args.a2a_listener_host,
        persist_allow_parents=args.persist_allow_parent or None,
        shutdown_drain_timeout_ms=args.shutdown_drain_timeout_ms,
    )

    # Setup signal handlers
    loop = asyncio.get_running_loop()
    def _signal_handler(sig):
        log.info("received signal %s, initiating shutdown", sig.name)
        asyncio.create_task(host.shutdown())
    for sig_name in ('SIGTERM', 'SIGINT'):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _signal_handler, sig)
            except (NotImplementedError, RuntimeError):
                pass  # Windows: not supported, rely on KeyboardInterrupt

    try:
        await host.start()
    except Exception as e:
        log.exception("host start failed: %s", e)
        return 1

    try:
        await host.run_forever()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt, shutting down")
        await host.shutdown()
    return 0


def cmd_host(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_run_host(args))
    except KeyboardInterrupt:
        return 130


# ---------- doctor (v1.5.2) ----------

def _format_doctor_line(r) -> str:
    icons = {"ok": "OK  ", "info": "INFO", "warn": "WARN", "fail": "FAIL"}
    return f"{icons.get(r.status, '?   ')} {r.name}: {r.message}"


def cmd_doctor(args: argparse.Namespace) -> int:
    """v1.5.2: print a deployment-health report.

    The doctor never fails the process based on individual checks; it
    exits 0 unless something went catastrophically wrong while collecting
    results (e.g. plugin loading raised). All other findings are
    surfaced as WARN/FAIL lines for an operator to read.
    """
    from . import __version__
    from .core import doctor as doctor_mod

    print(f"AgentWire CUE Doctor v{__version__}")
    print("=" * 32)

    if args.a2a_token_file:
        print(_format_doctor_line(doctor_mod.check_token_file(Path(args.a2a_token_file))))
    if args.admin_token_file:
        print(_format_doctor_line(doctor_mod.check_token_file(Path(args.admin_token_file))))

    if not args.no_network:
        print(_format_doctor_line(doctor_mod.check_core_reachable(args.a2a_url)))

    print(_format_doctor_line(doctor_mod.check_port_available(args.a2a_listener_port)))
    print(_format_doctor_line(doctor_mod.check_port_available(args.admin_port)))

    print(_format_doctor_line(doctor_mod.check_proxy_env()))

    if args.plugin_dir:
        plugin_dir = Path(args.plugin_dir)
        if not args.no_network:
            for r in doctor_mod.check_peers_reachable(
                doctor_mod.collect_peers_from_plugins(plugin_dir),
            ):
                print(_format_doctor_line(r))
        print(_format_doctor_line(doctor_mod.check_plugin_dependencies(plugin_dir)))

    return 0


# ---------- entry point ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentwire-cue",
        description="AgentWire-Cue v1.4 plugin host — validate and host cue.yaml",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_val = sub.add_parser("validate", help="validate plugin yaml")
    p_val.add_argument("path", help="plugin file or directory")
    p_val.set_defaults(func=cmd_validate)

    p_host = sub.add_parser("host", help="start the cue host process")
    p_host.add_argument("--plugin-dir", required=True, type=Path, help="directory of plugin yaml files")
    p_host.add_argument("--a2a-url", default="http://127.0.0.1:18800", help="AGENTWIRE A2A endpoint")
    p_host.add_argument("--a2a-token", default=None, help="Bearer token for AGENTWIRE (argv, visible in ps)")
    p_host.add_argument("--a2a-token-env", default=None,
                        help="read Bearer token from env var (avoids argv leak; takes precedence over --a2a-token)")
    p_host.add_argument("--a2a-token-file", default=None,
                        help="read Bearer token from file (BOM auto-stripped); takes precedence over --a2a-token-env")
    p_host.add_argument("--admin-token", default=None, help="Bearer token for admin API (D5a, required for prod)")
    p_host.add_argument("--admin-token-env", default=None,
                        help="read admin token from env var; takes precedence over --admin-token")
    p_host.add_argument("--admin-token-file", default=None,
                        help="read admin token from file; takes precedence over --admin-token-env")
    p_host.add_argument("--admin-port", type=int, default=19000, help="admin API port (default 19000)")
    p_host.add_argument("--admin-host", default="127.0.0.1", help="admin API bind host (default 127.0.0.1; use 0.0.0.0 only behind firewall/VPN)")
    p_host.add_argument("--a2a-listener-port", type=int, default=18801, help="A2A listener port (default 18801)")
    p_host.add_argument("--a2a-listener-host", default="127.0.0.1", help="A2A listener bind host (default 127.0.0.1; use 0.0.0.0 only behind firewall/VPN)")
    p_host.add_argument("--persist-allow-parent", action="append", default=[], help="extra allowed parent (repeatable)")
    p_host.add_argument("--shutdown-drain-timeout-ms", type=int, default=30_000, help="shutdown drain timeout (default 30s)")
    p_host.set_defaults(func=cmd_host)

    p_ver = sub.add_parser("version", help="print host version")
    p_ver.set_defaults(func=lambda _: (print(f"agentwire-cue {__version__}"), 0)[1])

    p_doc = sub.add_parser("doctor", help="v1.5.2 deployment health checks")
    p_doc.add_argument("--plugin-dir", default=None, type=Path,
                       help="directory of plugin yaml files (enables plugin-dep + peer checks)")
    p_doc.add_argument("--a2a-url", default=os.environ.get("CUE_DOCTOR_A2A_URL", "http://127.0.0.1:18800"),
                       help="AGENTWIRE CORE base URL to probe")
    p_doc.add_argument("--a2a-token-file", default=None,
                       help="optional: validate this token file for BOM/CRLF")
    p_doc.add_argument("--admin-token-file", default=None,
                       help="optional: validate this admin token file for BOM/CRLF")
    p_doc.add_argument("--a2a-listener-port", type=int, default=18801,
                       help="A2A listener port to probe (default 18801)")
    p_doc.add_argument("--admin-port", type=int, default=19000,
                       help="admin API port to probe (default 19000)")
    p_doc.add_argument("--no-network", action="store_true",
                       help="skip CORE and peer reachability probes")
    p_doc.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())


def _read_token_file(path: str) -> str:
    """v1.4.2 BUG-1: token file with UTF-8 BOM handled via utf-8-sig encoding."""
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read().strip()


def _resolve_secret(
    arg_value: str | None,
    arg_env: str | None,
    arg_file: str | None,
    default_env: str,
    name: str,
) -> str | None:
    """v1.4.2 fix: 4-source token resolution (arg > arg_env > arg_file > default_env).

    Priority: explicit value > explicit env var > explicit file > default env var.
    Missing explicit env var (when --xxx-token-env specified but unset) is an error
    (fail-fast via SystemExit).
    """
    import os, sys
    if arg_value:
        return arg_value
    if arg_env:
        val = os.environ.get(arg_env)
        if val is None:
            print(f"error: --{name}-env={arg_env} specified but env not set", file=sys.stderr)
            sys.exit(1)
        return val
    if arg_file:
        return _read_token_file(arg_file)
    return os.environ.get(default_env)


