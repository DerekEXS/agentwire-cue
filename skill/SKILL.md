# AgentWire-Cue SKILL (v1.6.1)

> **Language**: English | [中文版 (SKILL_CN.md)](SKILL_CN.md)

## What is AgentWire-Cue?

AgentWire-Cue is a **YAML statechart plugin host** running on top of [AgentWire A2A v1.0.1](https://a2a-protocol.org). You write workflow plugins as `*.yaml` files under the `agentwire/v1.2` schema; the host loads them, evaluates triggers, and executes state transitions and actions.

Cue is the **behavior** layer. The AgentWire CORE gateway is the **protocol + history** layer. Cue connects to CORE via A2A JSON-RPC, consumes message history through `peers.*` and `history.*` namespace expressions, and sends reactions back through `send_a2a` actions.

## Architecture

```
┌────────────────────────────────────────────────┐
│       AgentWire CORE (18800)                    │
│       - JSONL history (per-peer)                │
│       - JSON-RPC API                            │
│       - Bearer-token auth (hmac)                │
└─────────────┬──────────────────────────────────┘
              │ HTTP + Bearer
              ▼
┌────────────────────────────────────────────────┐
│       AgentWire-Cue host                       │
│       - loads plugins from dir                 │
│       - evaluates statecharts                  │
│       - polls messages/peers for history triggers│
│       - peers.Pawly.history.last_inbound_contains()│
│       - 18801: A2A inbound (admin-token gated) │
│       - 19000: admin API (token gated)         │
└────────────────────────────────────────────────┘
        ▲
        │ *.yaml plugins
        │
   Plugin author (you)
```

## Quick start

### Recommended: Docker Compose

```bash
cd agentwire_cue
mkdir -p secrets
printf '%s\n' 'YOUR_A2A_TOKEN' > secrets/a2a-token.txt
printf '%s\n' 'YOUR_ADMIN_TOKEN' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt
docker compose up -d
curl http://127.0.0.1:19000/admin/status \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

### Alternative: native Python

```bash
pip install aiohttp ruamel.yaml jsonschema croniter structlog
python3 -m agentwire_cue host \
  --plugin-dir ./examples \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/a2a-token.txt \
  --admin-token-file /tmp/cue-admin-token.txt \
  --admin-port 19000 \
  --a2a-listener-port 18801
```

> From v1.5.5, the listener and admin bind `127.0.0.1` by default. Use `--a2a-listener-host 0.0.0.0` / `--admin-host 0.0.0.0` only behind firewall/VPN.

## Plugin YAML anatomy

```yaml
apiVersion: agentwire/v1.2
kind: plugin

metadata:
  name: my-plugin
  version: 1.0.0
  description: "example statechart plugin"

spec:
  triggers:
    - id: on-cron
      type: cron
      config:
        expression: "0 8 * * *"
        timezone: "Asia/Shanghai"

    - id: on-pawly-urgent
      type: history_change
      config:
        peer: "Pawly"
        granularity: round
        poll_interval_seconds: 15

    - id: on-a2a
      type: a2a_message_type
      config: { match: "*" }

  # v1.5.2: cross-plugin dependency declarations
  requires:
    plugins: []
    peers: [Pawly]
    capabilities: [metadata]

  # v1.4.8: peer alias table
  peers:
    Pawly:
      uuid: "pawly-demo-uuid"
      url: "http://agentwire-core:18800"
      description: "小爪 - QwenPaw"
    main:
      uuid: "main-demo-uuid"
      url: "http://127.0.0.1:18800"
      description: "主 agent"

  statechart:
    initial: watching
    context:
      last_notified_round: 0

    states:
      watching:
        "on":
          history_change:
            target: watching
            guard: "(peers.Pawly.history.last_inbound_contains('urgent:') || peers.初梦.history.last_inbound_contains('urgent:')) && event.new_round > context.last_notified_round"
            actions:
              - type: set_context
                with: { key: last_notified_round, value: "{{event.new_round}}" }
              - type: send_a2a
                with:
                  peer: main
                  message:
                    type: A2A_MESSAGE
                    text: "urgent from {{event.peer}}"
                  metadata:
                    workflow_pointer: { step: "review" }

  secrets: []
  permissions:
    network: { http_egress: ["agentwire-core", "127.0.0.1"], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers:
      - id: main
        allow_messages: ["*"]
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
```

## Expression namespaces

| Namespace | Example | Notes |
|-----------|---------|-------|
| `event` | `event.type`, `event.peer`, `event.new_round` | Trigger payload |
| `context` | `context.last_notified_round` | Plugin state (persisted) |
| `state` | `state.duration_ms` | Current state metadata |
| `meta` | `meta.name` | Plugin metadata |
| `now` | `now` (function) | Current timestamp |
| `peers` | `peers.Pawly.history.last(5)` | **v1.4.3** — per-peer proxy |
| `history` | `history.total_rounds()` | **v1.4.3** — cross-peer aggregation |

Methods on `peers.<name>.history`:
- `last(n=5)` → list of last n messages
- `last_n_rounds(n=5)` → alias
- `count()` → total rounds
- `last_round()` → highest round number
- `last_inbound_contains(needle)` → bool; may raise `HistoryDiagnosticError` on empty peer
- `last_outbound_contains(needle)` → bool

`history.*` cross-peer:
- `total_rounds()` → sum across all peers
- `peer_count()` → number of known peers
- `peer_names()` → list of names

## Triggers

| Type | Fires when… | Config fields |
|------|-------------|---------------|
| `cron` | Cron expression matches | `expression`, `timezone` |
| `a2a_message_type` | Inbound A2A message arrives on 18801 | `match: "*"` or specific type string |
| `history_change` (v1.4.3) | Peer round count changes | `peer`, `granularity: round`, `poll_interval_seconds` |

## Actions

| Action | Purpose |
|--------|---------|
| `log` | Write a log line at level (info/warn/error) |
| `set_context` / `increment_context` | Modify statechart context |
| `send_a2a` | Send an A2A message to a peer via CORE; accepts `metadata` block |
| `reply_a2a` | Reply to the current inbound message |
| `http_request` | HTTP call (sandboxed by `http_egress`) |
| `write_file` / `read_file` | File I/O in the persist sandbox |
| `spawn_subprocess` | Run a command (must be in `subprocess.allow`) |

## Admin API (port 19000, token gated)

| Endpoint | Returns |
|----------|---------|
| `GET /admin/status` | Per-plugin runtime state, `last_trigger_at`, `last_match`, `last_reason`, uptime |
| `GET /admin/peers` | Peer alias table with **redacted** uuid (first 6 + `...`) and url (scheme + host + port); includes cached reachability probe |
| `GET /admin/plugins` | Loaded plugin names and count |
| `POST /plugins/{name}/trigger` | Fire a trigger on a plugin manually (admin diagnostics) |

## `agentwire-cue doctor`

```bash
python3 -m agentwire_cue doctor \
  --a2a-listener-port 18801 --admin-port 19000 --no-network
```

Checks: token-file hygiene (BOM/CRLF/perms), CORE reachability (downgraded to INFO in container DNS contexts), port availability, proxy env leaks, and plugin dependency completeness.

In the Docker image, `CUE_DOCTOR_A2A_URL=http://agentwire-core:18800` is pre-set so the healthcheck probes the correct container hostname.

## Security defaults (v1.5.5+)

- **Inbound listener**: binds `127.0.0.1`; Bearer-auth gated with the CUE admin token (breaking change: caller must use admin token, not the old A2A token).
- **Admin API**: binds `127.0.0.1`; `hmac.compare_digest` token check.
- **send_a2a**: When `permissions.peers` is non-empty, only listed peer ids are allowed.
- **Compose**: publishes ports on `127.0.0.1` by default. Remove the `127.0.0.1:` prefix only behind firewall/VPN/TLS.

## See also

- [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md) — detailed field reference
- [EXPRESSION_REFERENCE.md](EXPRESSION_REFERENCE.md) — expression grammar
- [INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md](INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md) — platform integration
