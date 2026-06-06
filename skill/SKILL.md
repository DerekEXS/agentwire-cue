# AgentWire-Cue SKILL

> **Language**: English | [中文版 (SKILL_CN.md)](SKILL_CN.md)

## What is AgentWire-Cue?

AgentWire-Cue is a **YAML-driven statechart engine** that runs as a plugin host on top of the [AgentWire A2A v1.0.1](https://a2a-protocol.org) gateway. You define plugin workflows as `*.yaml` files; the host loads them, evaluates state transitions, and executes actions.

Cue is the **state and behavior** layer. The AgentWire CORE gateway is the **protocol and persistence** layer. Cue connects to CORE via A2A JSON-RPC, consumes its history, and feeds actions back through A2A messages.

## Architecture

```
┌────────────────────────────────────────────────┐
│       AgentWire CORE (18800) — protocol         │
│       - history.jsonl (per-peer)                │
│       - JSON-RPC API                            │
└─────────────┬──────────────────────────────────┘
              │ HTTP + Bearer
              ▼
┌────────────────────────────────────────────────┐
│       AgentWire-Cue host                       │
│       - loads plugins/*.yaml                   │
│       - evaluates statecharts                  │
│       - polls /messages/peers for triggers     │
│       - history.peer("X").last(5) in exprs     │
│       - 18801: A2A listener (cues → peers)     │
│       - 19000: admin API (Bearer)               │
└────────────────────────────────────────────────┘
        ▲
        │ reads *.yaml
        │
   Plugin author (you)
```

## Plugin YAML anatomy

```yaml
id: my_plugin
version: 1.0.0
description: What this plugin does.

triggers:
  - name: on_cron
    type: cron
    expression: "0 8 * * *"
  - name: on_pawly_round
    type: history_change      # v1.4.3 new
    peer: "Pawly"
    granularity: round
  - name: on_a2a_msg
    type: a2a_message_type
    match: "*"

statechart:
  initial: idle
  context:
    foo: "bar"

  states:
    idle:
      transitions:
        - when: "peers.Pawly.last_round > 0"
          target: processing
        - when: "history.total_rounds() > 100"
          target: high_traffic
      on_enter:
        - action: log
          params: { message: "Entered idle" }

    processing:
      on_enter:
        - action: http_request
          params:
            url: "http://127.0.0.1:18800/a2a/jsonrpc"
            method: POST
            body:
              jsonrpc: "2.0"
              id: 1
              method: "messages/list"
              params: { peer_name: "Pawly", limit: 5 }
          save_as: context.recent
      transitions:
        - when: "context.recent != null"
          target: done

    high_traffic: {}
    done: {}
```

## Expression namespaces (v1.4.3+)

Cue expressions support these top-level namespaces:

| Namespace | Example | Notes |
|-----------|---------|-------|
| `event` | `event.type` | Trigger event payload |
| `context` | `context.user_name` | Plugin state (persisted) |
| `state` | `state.duration_ms` | Current state metadata |
| `meta` | `meta.plugin_id` | Plugin metadata |
| `now` | `now` (function) | Current timestamp |
| `peers` | `peers.Pawly.history.last(5)` | **v1.4.3** — per-peer |
| `history` | `history.total_rounds()` | **v1.4.3** — cross-peer |

`peers.<name>` returns a peer proxy. Methods on `peers.<name>.history`:
- `last(n=5)` → list of last n messages
- `last_n_rounds(n=5)` → alias for `last(n)`
- `count()` → total rounds stored
- `last_round()` → highest round number
- `last_inbound_contains(needle)` → bool
- `last_outbound_contains(needle)` → bool

`history.*` is for cross-peer:
- `total_rounds()` → sum across all peers
- `peer_count()` → number of known peers
- `peer_names()` → list of names

## Triggers

| Type | Fires when… | Example |
|------|-------------|---------|
| `cron` | Cron expression matches | `{ type: cron, expression: "0 8 * * *", timezone: "Asia/Shanghai" }` |
| `a2a_message_type` | Inbound A2A message matches | `{ type: a2a_message_type, match: "request" }` |
| `history_change` (v1.4.3) | A peer's round count changes | `{ type: history_change, peer: "Pawly", granularity: round, poll_interval_seconds: 30 }` |

## Actions

Cue plugins can invoke these action types:

| Action | Purpose |
|--------|---------|
| `http_request` | Make an HTTP call (sandboxed) |
| `write_file` | Write a file in the persist dir |
| `read_file` | Read a file from the persist dir |
| `spawn_subprocess` | Run a shell command (sandboxed) |
| `log` | Write a log line |
| `send_a2a` | Send an A2A message to a peer via CORE |

## Sandbox & permissions

Cue enforces a 4-layer path sandbox:
- **L1 default**: plugin's persist dir
- **L2 spec**: paths declared in plugin's `permissions.persist.allowed_parents`
- **L3 CLI**: paths from `--persist-allow` flag
- **L4 blocked**: hardcoded block list (e.g. `/etc`, `~/.ssh`)

## Companion: AgentWire CORE

Cue is **not** a standalone A2A gateway. It depends on the AgentWire CORE service for:
- HTTP endpoint and JSON-RPC protocol
- Bearer-token authentication
- Per-peer message history persistence
- Redaction pattern catalog

To run cue, you must also have CORE running on the same host (default `127.0.0.1:18800`).

## Quick start

```bash
# 1. Start CORE
cd ../agentwire_core/server
python3 start.py --host 127.0.0.1 --port 18800 --token-file /tmp/token.txt

# 2. Install cue
cd ../../agentwire_cue
pip install aiohttp ruamel.yaml jsonschema croniter structlog

# 3. Write a plugin
mkdir -p plugins
cat > plugins/hello.yaml <<'YAML'
id: hello
version: 1.0.0
triggers:
  - name: on_message
    type: a2a_message_type
    match: "*"
statechart:
  initial: greeted
  states:
    greeted:
      on_enter:
        - action: log
          params: { message: "Hello from cue!" }
YAML

# 4. Run cue host
python3 -m agentwire_cue host \
  --plugin-dir ./plugins \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/token.txt \
  --admin-token "admin-secret"

# 5. Send a message to CORE; cue will react
curl -X POST http://127.0.0.1:18800/a2a/rest/message/send \
  -H "Authorization: Bearer $(cat /tmp/token.txt)" \
  -H "Content-Type: application/json" \
  -d '{"message":{"parts":[{"type":"text","text":"hi"}]}}'
```

## See also

- [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md) — detailed plugin authoring guide
- [EXPRESSION_REFERENCE.md](EXPRESSION_REFERENCE.md) — full expression grammar
- [INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md](INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md) — running cue on different platforms

## License

MIT License. Copyright (c) 2026 DerekEXS. See [LICENSE](../LICENSE).
