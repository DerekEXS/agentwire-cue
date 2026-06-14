# AgentWire-Cue

> **YAML-driven statechart engine** for A2A v1.0.1 agents — runs on top of [AgentWire-Core](https://github.com/DerekEXS/agentwire-core).
>
> **Languages**: [English](README.md) (this file) | [简体中文](README_CN.md)

[![A2A Protocol](https://img.shields.io/badge/A2A-v1.0.1-blue)](https://a2a-protocol.org/latest/specification/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-v1.6.1-green)](https://github.com/DerekEXS/agentwire-cue/releases/tag/v1.6.1)

---

## What is AgentWire-Cue?

AgentWire-Cue is a **plugin host** that loads YAML-defined statecharts and reacts to events on the A2A v1.0.1 wire. Cue consumes AgentWire-Core's A2A service: it reads message history via JSON-RPC, evaluates state guards against the latest rounds, and fires actions back through the gateway.

Use cue when you want to:
- Define agent behaviors as **declarative YAML** (no Python boilerplate)
- Trigger workflows from **message history** (e.g. "Pawly just said X")
- Run **cron-style** background tasks that consult conversation context
- Orchestrate **multi-peer** conversations across QwenPaw / OpenClaw / Hermes / Claude

Cue is **not** an agent framework. It does not generate responses. It executes the behaviors *you* describe in YAML.

## Features

- **YAML plugin format** — triggers, statechart, actions, permissions (apiVersion `agentwire/v1.2`)
- **Three trigger types** — `cron`, `a2a_message_type`, and `history_change` (v1.4.3)
- **Expression engine** — `peers.<name>.history.*`, `history.*`, `event.*`, `context.*`, `meta.*`, `now.*` namespaces; method-call syntax `peers.Pawly.history.last_inbound_contains("keyword")` (v1.4.3)
- **`spec.peers` aliases** — peer UUID/URL table for stable history lookup and direct A2A routing (v1.4.8)
- **`spec.requires` dependencies** — cross-plugin `plugins` / `peers` / `capabilities` dependency checking (v1.5.2)
- **`send_a2a` with metadata** — optional `metadata` blocks with template rendering (v1.4.8)
- **`send_a2a` workflow-pointer** — `metadata.workflow_pointer` sets the stage for A2A task handoff (v1.5.0)
- **`spec.resilience.on_exhaust`** — loader-time validation against declared states (v1.5.9)
- **`permissions.peers` enforcement** — non-empty peer allowlists gate `send_a2a` calls (v1.5.5)
- **Admin API on port 19000** — `/admin/status`, `/admin/peers` (uuid/url redacted, 30s reachability cache), `/admin/plugins` (v1.5.1/v1.5.6)
- **`agentwire-cue doctor`** — comprehensive pre-flight check: token file BOM/CRLF/chmod, CORE reachability (with container-downgrade), port conflicts, plugin dependency completeness (v1.5.2/v1.5.7)
- **Structured observability** — trace-id-per-trigger, `cue.trigger.*` / `cue.guard.*` / `cue.action.*` / `cue.send_a2a.*` / `cue.error` JSON-line events (v1.5.1)
- **Security defaults** — A2A listener + admin API default `127.0.0.1`; `/a2a/inbound` requires admin token (v1.5.5); non-loopback+no-token blocks inbound (v1.5.6)
- **4-layer path sandbox** for file and subprocess actions
- **330+ unit tests** covering grammar, actions, permissions, admin, doctor

## Architecture

```
┌────────────────────────────────────────────────┐
│   AgentWire-Core gateway (18800)               │
│   - JSON-RPC + per-peer history                │
└─────────────┬──────────────────────────────────┘
              │ A2A JSON-RPC over HTTP
              ▼
┌────────────────────────────────────────────────┐
│   AgentWire-Cue host                           │
│   ┌────────────────────────────────────────┐  │
│   │  Plugin loader (plugins/*.yaml)        │  │
│   │  Expression engine (event/context/     │  │
│   │    state/meta/peers/history/now)       │  │
│   │  Action dispatcher (http/write/etc)    │  │
│   │  4-layer sandbox + permission enforcer │  │
│   │  Trigger scheduler (cron/a2a/history)  │  │
│   │  Peer card cache (10min TTL)           │  │
│   └────────────────────────────────────────┘  │
│   18801: A2A listener (inbound from peers)    │
│   19000: Admin API (Bearer-protected)          │
└────────────────────────────────────────────────┘
```

## Quick Start

Cue requires AgentWire-Core. The recommended way is Docker Compose:

```bash
# 1. Clone the CUE repo (contains the unified compose for CORE + CUE)
git clone https://github.com/DerekEXS/agentwire-cue.git
cd agentwire-cue

# 2. Prepare secrets
mkdir -p secrets
printf '%s\n' 'YOUR_A2A_TOKEN' > secrets/a2a-token.txt
printf '%s\n' 'YOUR_CUE_ADMIN_TOKEN' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt

# 3. Start CORE + CUE
docker compose up -d

# 4. Verify
docker compose ps
curl -s http://127.0.0.1:18800/.well-known/agent.json
curl -s http://127.0.0.1:18801/.well-known/agent.json

# 5. Run in-container doctor
docker exec agentwire-cue python3 -m agentwire_cue doctor --no-network
```

CORE listens on `127.0.0.1:18800`; CUE A2A listener on `127.0.0.1:18801`; admin API on `127.0.0.1:19000`.

### Standalone (Python directly)

```bash
# 1. Install dependencies
pip install aiohttp ruamel.yaml jsonschema croniter structlog

# 2. Start AgentWire-Core first (see its README)
# 3. Start CUE host
python3 -m agentwire_cue host \
  --plugin-dir ./plugins \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/agentwire.token \
  --admin-port 19000 \
  --admin-token "admin-secret"
```

## Plugin example

`plugins/greet.yaml`:

```yaml
id: greet
version: 1.0.0
description: Log a greeting on the first A2A message.

permissions:
  persist: { allowed_parents: [] }
  network: { http_egress: [] }

triggers:
  - name: on_a2a_message
    type: a2a_message_type
    match: "*"

statechart:
  initial: greeting
  context: { greeted: false }

  states:
    greeting:
      on_enter:
        - action: log
          params: { message: "Hello from cue!" }
        - action: set_context
          params: { key: greeted, value: true }
      transitions:
        - { target: idle }

    idle: {}
```

A v1.4.8+ plugin using history with peer aliases:

```yaml
id: pawly_responder
version: 1.0.0

peers:
  Pawly:
    uuid: "Pawly-demo-uuid"
    url: "http://pawly:18800"

triggers:
  - name: pawly_replied
    type: history_change
    peer: "Pawly"
    granularity: round
    poll_interval_seconds: 30

statechart:
  initial: watching

  states:
    watching:
      transitions:
        - when: "peers.Pawly.history.last_inbound_contains('project:')"
          target: kickoff

    kickoff:
      on_enter:
        - action: send_a2a
          params:
            peer: "Pawly"
            text: "Got the project brief. Starting work on round {{ peers.Pawly.last_round }}."
      transitions:
        - { target: watching }
```

For more details, see [`skill/PLUGIN_AUTHORING.md`](skill/PLUGIN_AUTHORING.md).

## CLI

```bash
# Run host
python3 -m agentwire_cue host --plugin-dir ./plugins --a2a-url http://127.0.0.1:18800

# Validate a plugin
python3 -m agentwire_cue validate plugins/my_plugin.yaml

# Trigger a plugin manually
python3 -m agentwire_cue trigger my_plugin manual --payload '{"foo": "bar"}'

# Run pre-flight doctor (local checks only)
python3 -m agentwire_cue doctor --no-network

# Run pre-flight doctor (full, including CORE reachability probes)
python3 -m agentwire_cue doctor
```

## Documentation

- [skill/SKILL.md](skill/SKILL.md) — quickstart overview
- [skill/PLUGIN_AUTHORING.md](skill/PLUGIN_AUTHORING.md) — full YAML field reference (v1.2 schema + all v1.5.x actions)
- [skill/EXPRESSION_REFERENCE.md](skill/EXPRESSION_REFERENCE.md) — expression grammar (peers.*, history.*, event.*, context.*, meta.*)
- [skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md](skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md) — platform integration
- [README-DOCKER.md](README-DOCKER.md) — Docker deployment details + migration from systemd

## Companion project

AgentWire-Cue requires [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) running on the same host (default `127.0.0.1:18800`). CORE provides the A2A v1.0.1 protocol surface (JSON-RPC, REST), Bearer-token authentication with HMAC constant-time compare, per-peer JSONL message history (auto-redacted), redaction pattern catalog, TLS support, and metrics.

## Repository Structure

```
agentwire-cue/
├── __init__.py                 # __version__ = "1.6.0"
├── __main__.py                 # CLI entrypoint
├── core/                       # Library code
│   ├── host.py                 # Plugin host: load → startup → trigger registration
│   ├── statechart.py           # Expression-based statechart engine (guard eval, actions)
│   ├── expression.py           # Tokenizer + parser, method-call syntax
│   ├── loader.py               # YAML loader + schema validation + on_exhaust checks
│   ├── a2a_client.py           # A2A HTTP client + retry policy + peer card cache
│   ├── history_client.py       # CORE JSON-RPC history proxy
│   ├── history_proxy.py        # PeersNamespace for expression evaluation
│   ├── permission.py           # PermissionEnforcer (persist/network/subprocess/secret/admin)
│   ├── observability.py        # Structured JSON-line observability (trace_id, emit)
│   ├── redact.py               # RedactClient (pattern pull from CORE /redact/patterns)
│   ├── trigger_impl.py         # HistoryChangeTrigger config polling
│   ├── doctor.py               # Pre-flight health checks
│   ├── sandbox.py              # 4-layer path sandbox
│   └── ...
├── skill/                      # Agent-facing documentation
│   ├── SKILL.md / SKILL_CN.md
│   ├── PLUGIN_AUTHORING.md
│   ├── EXPRESSION_REFERENCE.md
│   └── INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md
├── tests/                      # 330+ unit tests (334 passed, 6 skipped)
├── examples/                   # Demo cue plugins
│   ├── echo-with-persist.yaml  # v0.4.0
│   ├── cron-driven.yaml
│   ├── a2a-with-fallback.yaml
│   ├── file-watcher.yaml
│   └── owner-alert/cue.yaml    # history_change killer example
├── schema/
│   └── plugin.schema.json      # agentwire/v1.2 JSON Schema
├── Dockerfile                  # v1.5.3: python:3.13-slim, non-root agentwire user
├── docker-compose.yml          # Unified CORE + CUE Compose (loopback-published)
├── README.md
└── README-DOCKER.md
```

## Protocol

- **A2A v1.0.1** — <https://a2a-protocol.org/latest/specification/>
- **JSON-RPC 2.0** — <https://www.jsonrpc.org/specification>

## Deployment

> **Docker Compose is the canonical deployment method** (CUE v1.6.1+). See the repo root `docker-compose.yml`.

Docker images: CORE `agentwire-core:v1.5.5` / CUE `agentwire-cue:v1.6.1`. Both bind `127.0.0.1` by default.
All ports are published on host-loopback only.

### Docker Compose (recommended)

```bash
cd agentwire-cue
mkdir -p secrets
printf '%s\n' 'YOUR_A2A_TOKEN' > secrets/a2a-token.txt
printf '%s\n' 'YOUR_CUE_ADMIN_TOKEN' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt
docker compose up -d
```

See [`README-DOCKER.md`](README-DOCKER.md) for migration from old systemd/nohup deployments,
history volume migration, and production owner-alert configuration.

### Security notes

- A2A listener + admin API both default `127.0.0.1` (since v1.5.5). Bind `0.0.0.0` requires
  explicit `--a2a-listener-host 0.0.0.0` / `--admin-host 0.0.0.0` flags and logs a
  startup warning.
- `/a2a/inbound` requires the CUE admin token for Bearer auth (v1.5.5 breaking change —
  old A2A tokens are no longer accepted).
- Non-loopback bind with no auth token configured returns HTTP 403 on inbound (v1.5.6).
- `send_a2a` enforces `permissions.peers` allowlists when present (v1.5.5).

**Suitable for**: loopback, private LAN, Tailscale, WireGuard

**NOT suitable for**: direct public-internet exposure without TLS-terminating reverse proxy

## References

- [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/)
- [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) (companion)
- [Releases](https://github.com/DerekEXS/agentwire-cue/releases)

## License

**MIT License** — Copyright (c) 2026 DerekEXS. See [LICENSE](LICENSE) for the full text.

You are free to use, modify, and distribute this software, with or without modification, provided the copyright notice and permission notice are preserved.
