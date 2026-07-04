# Cue Plugin Authoring Guide (v1.6.2)

> Detailed reference for writing `*.yaml` plugin files for AgentWire-Cue.
> See [SKILL.md](SKILL.md) for the high-level overview.
> Schema version: `agentwire/v1.2`.

## Security (v1.5.5+)

- `permissions.peers` is now enforced at `send_a2a` time: when non-empty, only listed peer ids are allowed. Empty = legacy permissive.
- `/a2a/inbound` requires the **CUE admin token** (not the A2A token).
- Inbound listener and admin API default to `127.0.0.1`.
- See [SKILL.md](SKILL.md) for the full security defaults.

## The 4 primitives

A plugin has exactly 4 sections: `triggers`, `statechart`, `permissions`, and metadata.

### 1. Metadata (top-level)

```yaml
id: my_plugin          # required, unique
version: 1.0.0         # required, semver
description: ...       # optional
```

### 2. Triggers

What makes the statechart react to the world.

```yaml
triggers:
  - name: morning_digest
    type: cron
    expression: "0 8 * * *"
    timezone: "Asia/Shanghai"  # IANA, required for cron

  - name: any_a2a_message
    type: a2a_message_type
    match: "*"  # or exact message type

  - name: <your-remote-peer-replied>     # v1.4.3
    type: history_change
    peer: "<your-remote_peer_alias>"     # matches a key in spec.peers
    granularity: round  # round | message | manual
    poll_interval_seconds: 30
```

### 3. Statechart

States, transitions, actions.

```yaml
statechart:
  initial: idle         # entry state
  context:              # initial context values
    counter: 0
  states:
    idle: { ... }
    busy: { ... }
    done: {}
```

### 4. Permissions

```yaml
permissions:
  persist:
    allowed_parents:        # L2 path sandbox
      - "/var/lib/myapp/data"
    exclude:                 # sensitive fields to omit from persisted context
      - "*.token"
      - "*.password"
  network:
    http_egress:             # allowed outbound HTTP hosts
      - "api.example.com"
    http_methods: ["GET", "POST"]
  subprocess:
    allow: ["echo", "date"]  # executable basenames
```

## `spec.peers` — peer alias table (v1.4.8+)

Stable history lookups + direct A2A routing. Each entry is keyed by alias name:

```yaml
spec:
  peers:
    remote_peer_a:
      uuid: "<set-me-remote_peer_a-uuid>"
      url: "http://<set-me-remote_peer_a-host>:18800"
      description: "<set-me-remote_peer_a description>"
    main:
      uuid: "main-demo-uuid"
      url: "http://127.0.0.1:18800"
      description: "初梦 - OpenClaw @ 本地"
```

`uuid` and `url` are required. The alias table is installed on both the
shared `HistoryClient` and the `A2AClient` so expressions and outbound sends
both resolve the alias transparently.

### Per-peer A2A token (v1.6.1+)

When CUE needs to access a remote peer's CORE (history queries, `send_a2a`
delivery), it must use that peer's A2A token, not the local CORE token.
Each peer entry can carry its own auth:

| Field | Type | Description |
|-------|------|-------------|
| `token` | str | Literal token value (avoid in git-tracked YAMLs) |
| `token_env` | str | Read token from this env var (recommended) |
| `token_file` | str | Read token from this file path (Docker secrets friendly) |

**Resolution priority**: `token_file` > `token_env` > `token` (literal) >
default local CORE token. If a peer has none of the three fields, CUE
falls back to the local CORE token unchanged (backward compatible).

```yaml
spec:
  peers:
    # NOTE: 'Pawly' below is a SLOT NAME example; replace with your actual
    # peer alias. NEVER commit real uuid / url / host here — use a
    # gitignored *.local.yaml overlay (see README-DOCKER.md).
    <your-remote-peer-name>:
      uuid: "<set-me-your-remote-peer-uuid>"
      url: "http://<set-me-your-remote-peer-host>:18800"
      # v1.6.1: per-peer auth for remote CORE
      token_env: "<YOUR_PEER>_A2A_TOKEN"     # recommended
      # token_file: "/run/secrets/<your-peer>-a2a-token.txt"  # Docker secrets
      # token: "<REDACTED>"                  # literal (NOT recommended)
```

**When to use**:
- Multi-CORE deployments where each CORE has its own A2A token
- CUE reads `peers.<alias>.history` in guard expressions (owner-alert style)
- CUE sends `send_a2a` to a remote peer

**Security recommendations**:
- Prefer `token_env` (Docker compose `environment`) or `token_file`
  (Docker secrets) over `token` literal values that may land in git
- `token_file` paths should be `chmod 600` and not world-readable
- `token_file` is read on every send/history call, so rotating credentials
  at runtime takes effect on the next call (no host restart needed)

## State anatomy

```yaml
states:
  state_name:
    on_enter:                # actions when entering
      - action: <type>
        params: { ... }
        save_as: context.some_key  # optional: stash result in context

    on_exit:                 # actions when leaving
      - action: <type>
        params: { ... }

    transitions:             # guards to other states
      - when: "<expression>"
        target: other_state
      - when: "<expression>"
        target: another_state
```

Transitions are tried in order. First matching `when` wins.

## Action reference

| Action | params | save_as target | Notes |
|--------|--------|----------------|-------|
| `http_request` | `url`, `method`, `headers?`, `body?` | the parsed response JSON | Network call |
| `read_file` | `path` | string contents | Sandbox-checked |
| `write_file` | `path`, `content` | — | Sandbox-checked |
| `spawn_subprocess` | `cmd`, `args?`, `cwd?` | `{stdout, stderr, returncode}` | Sandbox-checked |
| `log` | `message` | — | Goes to stdout |
| `send_a2a` | `peer`, `text` | — | Goes through CORE |

### `http_request` example

```yaml
- action: http_request
  params:
    url: "https://api.example.com/data"
    method: POST
    headers:
      Authorization: "Bearer {{ context.api_token }}"
    body:
      user: "{{ context.user }}"
      limit: 10
  save_as: context.api_response
```

## Expression grammar (v1.4.3)

### Top-level

```
expression   := or_expr
or_expr      := and_expr ("||" and_expr)*
and_expr     := not_expr ("&&" not_expr)*
not_expr     := "!" not_expr | cmp_expr
cmp_expr     := primary (cmp_op primary)?
cmp_op       := "==" | "!=" | ">" | "<" | ">=" | "<="
primary      := "(" expression ")" | literal | variable | function | method
literal      := number | string | true | false | null
variable     := identifier ("." identifier)*
function     := identifier "(" arglist? ")"
method       := variable "(" arglist? ")"   # v1.4.3 new
```

### Whitelisted namespaces

```
event.context.state.meta.now.peers.history
```

Anything else raises `ExpressionError`.

### Available functions

- `now()` — current timestamp in ms (also `now` as a value, not a function)
- `since(ts)` — seconds since `ts`
- `duration_in_state()` — ms in current state

### v1.4.3 method calls (on namespaces)

- `peers.<name>.last_round` — int (dot-path)
- `peers.<name>.total_rounds` — int (dot-path)
- `peers.<name>.history.last(n)` — list of messages
- `peers.<name>.history.count()` — int
- `peers.<name>.history.last_inbound_contains(needle)` — bool
- `history.total_rounds()` — int (cross-peer)
- `history.peer_count()` — int
- `history.peer_names()` — list[str]

## Templates

In action `params`, any string value supports `{{ ... }}` substitution:

```yaml
- action: log
  params:
    message: "Round {{ peers.<your-remote_peer_alias>.last_round }} reached for <your-remote_peer_alias>"
```

`{{ expr }}` is evaluated against the same env as guards. Returns `""` if value is None.

## Persistence

Cue auto-persists the statechart's `context` to the plugin's persist dir after every transition (filtered by `permissions.persist.exclude`).

On startup, the statechart is restored from the persisted file.

## Lint a plugin

```bash
python3 -m agentwire_cue validate plugins/my_plugin.yaml
```

Validates YAML structure, expression syntax, action params, and permission refs.

## Debug

Set `LOG_LEVEL=DEBUG` to see all transition evaluations and action results.

## Example: a complete plugin

```yaml
# plugins/morning_digest.yaml
id: morning_digest
version: 1.0.0
description: At 8am, summarize the last 24h of agent traffic.

permissions:
  persist:
    allowed_parents: []
  network:
    http_egress:
      - "127.0.0.1"

triggers:
  - name: morning
    type: cron
    expression: "0 8 * * *"
    timezone: "Asia/Shanghai"

statechart:
  initial: gathering
  context:
    template: ""
    recent: []

  states:
    gathering:
      on_enter:
        - action: http_request
          params:
            url: "http://127.0.0.1:18800/a2a/jsonrpc"
            method: POST
            body:
              jsonrpc: "2.0"
              id: 1
              method: "messages/list"
              params: { limit: 100 }
          save_as: context.recent
      transitions:
        - when: "context.recent != null"
          target: notifying

    notifying:
      on_enter:
        - action: send_a2a
          params:
            peer: "初梦"
            text: "Morning digest: {{ history.peer_count() }} active peers, {{ history.total_rounds() }} total rounds."
      transitions:
        - { target: done }

    done: {}
```
