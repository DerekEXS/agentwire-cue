# AgentWire-Cue

> **YAML-driven statechart engine** for A2A v1.0.1 agents — runs on top of [AgentWire-Core](https://github.com/DerekEXS/agentwire-core).
>
> **Languages**: [English](README.md) (this file) | [简体中文](README_CN.md)

[![A2A Protocol](https://img.shields.io/badge/A2A-v1.0.1-blue)](https://a2a-protocol.org/latest/specification/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![Status](https://img.shields.io/badge/status-v1.4.3-yellow)](https://github.com/DerekEXS/agentwire-cue/releases)

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

- **YAML plugin format** — 4 primitives: triggers, statechart, actions, permissions
- **Two trigger types** — `cron` and `a2a_message_type` (v1.4.3 adds `history_change`)
- **Two new namespaces** (v1.4.3) — `peers.<name>.history.*` and `history.*` for cross-peer queries
- **Method-call syntax** (v1.4.3) — `peers.Pawly.history.last_inbound_contains("project:")`
- **4-layer path sandbox** for file and subprocess actions
- **3 layers of HTTP egress** allow-list (spec / plugin / flag)
- **5-category permission enforcer** (persist / network / subprocess / secret / admin)
- **Bearer-token admin API** on port 19000 (`/status`, `/plugins`, `/trigger`)
- **Persistent context** with sensitive-field exclusion
- **250+ unit tests** covering grammar, actions, permissions

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

Cue requires AgentWire-Core to be running. Use cue alongside it.

```bash
# 1. Install cue
pip install aiohttp ruamel.yaml jsonschema croniter structlog

# 2. Start AgentWire-Core
git clone https://github.com/DerekEXS/agentwire-core.git
cd agentwire-core/server
pip install -r requirements.txt
echo "my-token-123" > /tmp/agentwire.token
python3 start.py --host 127.0.0.1 --port 18800 --token-file /tmp/agentwire.token
cd ../..

# 3. Start cue host
cd agentwire-cue
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

A v1.4.3 plugin using history:

```yaml
id: pawly_responder
version: 1.0.0

triggers:
  - name: pawly_replied
    type: history_change      # v1.4.3
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

## CLI

```bash
# Run host
python3 -m agentwire_cue host --plugin-dir ./plugins --a2a-url http://127.0.0.1:18800

# Validate a plugin
python3 -m agentwire_cue validate plugins/my_plugin.yaml

# Trigger a plugin manually
python3 -m agentwire_cue trigger my_plugin manual --payload '{"foo": "bar"}'

# Show status
python3 -m agentwire_cue status
```

## Documentation

- [skill/SKILL.md](skill/SKILL.md) — quickstart overview
- [skill/PLUGIN_AUTHORING.md](skill/PLUGIN_AUTHORING.md) — full plugin authoring guide
- [skill/EXPRESSION_REFERENCE.md](skill/EXPRESSION_REFERENCE.md) — expression grammar
- [skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md](skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md) — running on different platforms

## Companion project

AgentWire-Cue requires [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) running on the same host (default `127.0.0.1:18800`). CORE provides:

- The A2A v1.0.1 protocol surface (JSON-RPC, REST)
- Bearer-token authentication
- Per-peer message history persistence
- Redaction pattern catalog
- Metrics endpoint

## Repository Structure

```
agentwire-cue/
├── core/                      # Library code
│   ├── expression.py          # v1.4.3: + peers/history namespaces
│   ├── history_client.py      # v1.4.3 new
│   ├── history_proxy.py       # v1.4.3 new
│   ├── redact.py              # v1.4.3 new
│   ├── host.py                # v1.4.3: + history trigger wiring
│   ├── trigger_impl.py        # v1.4.3: + HistoryChangeTrigger
│   ├── statechart.py          # v1.4.3: + history_client in EvalEnv
│   ├── a2a_client.py
│   ├── loader.py
│   ├── permission.py
│   └── ...
├── skill/                     # v1.4.3 new: agent documentation
│   ├── SKILL.md / SKILL_CN.md
│   ├── PLUGIN_AUTHORING.md
│   ├── EXPRESSION_REFERENCE.md
│   └── INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md
├── examples/                  # Demo cue plugins
├── tests/                     # 250+ unit tests
├── schema/
│   └── plugin.schema.json
├── __main__.py                # CLI entrypoint
└── README.md
```

## Protocol

- **A2A v1.0.1** — <https://a2a-protocol.org/latest/specification/>
- **JSON-RPC 2.0** — <https://www.jsonrpc.org/specification>

## References

- [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/)
- [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) (companion)
- [Releases](https://github.com/DerekEXS/agentwire-cue/releases)

## License

**MIT License** — Copyright (c) 2026 DerekEXS. See [LICENSE](LICENSE) for the full text.

You are free to use, modify, and distribute this software, with or without modification, provided the copyright notice and permission notice are preserved.
