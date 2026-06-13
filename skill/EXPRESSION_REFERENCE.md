# Cue Expression Reference (v1.5.7)

> Full grammar and reference for AgentWire-Cue guard expressions and templates.
> See [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md) for usage examples.
>
> **Support**: `structlog` in Docker image, stdlib JSON fallback locally. `history_proxy.HistoryDiagnosticError` may raise on empty histories — guards using `||` will short-circuit, but empty peer lookups fail before reaching the `||` test.

## Grammar (EBNF)

```
expression   := or_expr
or_expr      := and_expr ( "||" and_expr )*
and_expr     := not_expr ( "&&" not_expr )*
not_expr     := "!" not_expr | cmp_expr
cmp_expr     := primary ( cmp_op primary )?
cmp_op       := "==" | "!=" | ">" | "<" | ">=" | "<="
primary      := "(" expression ")"
             | literal
             | variable
             | function_call
             | method_call
literal      := NUMBER | STRING | "true" | "false" | "null"
variable     := identifier ( "." identifier )*
function_call:= identifier "(" arg_list? ")"
method_call  := variable "(" arg_list? ")"      # v1.4.3 new
arg_list     := expression ( "," expression )*
```

## Whitelisted namespaces

Cue rejects any identifier outside this list:

```
event    — trigger event payload
context  — plugin state (persisted)
state    — current state metadata
meta     — plugin metadata
now      — current timestamp (ms)
peers    — v1.4.3, per-peer proxy
history  — v1.4.3, cross-peer aggregate
```

`secrets` and `env` are **explicitly denied**.

## Reserved tokens (cannot be used as identifiers)

- `true`, `false`, `null` — booleans / null literal
- `now` — function-style timestamp
- `since`, `duration_in_state` — built-in functions

## Comparison semantics (v1.2 spec §3.2)

**Strict, no implicit conversion.** `1 == "1"` is `false` (not `true`). Booleans only compare equal to booleans. Numbers compare as floats when mixed. Strings only compare equal to strings. `None` only equals `None`.

| LHS | RHS | `==` |
|-----|-----|------|
| int | int | int comparison |
| int | float | float comparison |
| int | string | **false** |
| string | string | string equality |
| bool | int | **false** (no `1 == true`) |
| None | None | true |
| None | anything else | false |

## Built-in functions

| Function | Signature | Returns |
|----------|-----------|---------|
| `now()` | — | current ms timestamp (integer) |
| `since(ts)` | int or float ms | `now() - ts` (ms) |
| `duration_in_state()` | — | ms in current state |

## `event.*` shape

For a trigger `a2a_message_type`:
- `event.type` — `"A2A_MESSAGE"` or matched type
- `event.message` — full inbound message dict
- `event.message_id` — A2A message id

For a trigger `history_change`:
- `event.type` — `"history_change"`
- `event.peer` — peer name
- `event.prev_round` — round number before change
- `event.new_round` — round number after change
- `event.new_count` — `new_round - prev_round`
- `event.granularity` — `round` / `message` / `manual`

For a trigger `cron`:
- `event.type` — `"cron"`
- `event.trigger_name` — name from the trigger def
- `event.fired_at_ms` — timestamp

## `context.*`

Anything you put in `statechart.context` plus any `save_as: context.X` results. Persisted across runs.

## `state.*`

- `state.id` — current state name
- `state.duration_ms` — ms in current state
- `state.entered_at_ms` — when state was entered

## `meta.*`

From the plugin's metadata block:
- `meta.id` — plugin id
- `meta.version` — plugin version
- `meta.description` — plugin description
- any custom fields from plugin yaml

## `peers.*` (v1.4.3)

`peers.<name>` returns a peer proxy. If the peer is unknown, returns an empty proxy (`.count() == 0`).

Available via dot path:
- `peers.<name>.uuid` — peer UUID string
- `peers.<name>.name` — display name
- `peers.<name>.last_round` — int
- `peers.<name>.total_rounds` — int
- `peers.<name>.last_ts` — ISO timestamp

Available via method call on `peers.<name>.history`:
- `last(n=5)` — list of last n message dicts
- `last_n_rounds(n=5)` — alias
- `count()` — int
- `last_round()` — int
- `last_inbound_contains(needle)` — bool (searches recent inbound)
- `last_outbound_contains(needle)` — bool

## `history.*` (v1.4.3)

Cross-peer aggregations. Methods only:
- `total_rounds()` — sum across all known peers
- `total_rounds_today()` — v1.4.3 simple: same as total_rounds (per-message date filter deferred)
- `peer_count()` — number of known peers
- `peer_names()` — list of display names

## Template substitution

In action `params` (any string value):

```yaml
- action: log
  params:
    message: "Hello, {{ peers.Pawly.name }} — round {{ peers.Pawly.last_round }}"
```

`{{ expr }}` is evaluated against the same env. None values render as empty string. Booleans render as `true`/`false`. Numbers and strings render as their string form.

## Errors

| Error | When |
|-------|------|
| `ExpressionError: namespace 'X' not in whitelist` | Used a non-whitelisted top-level namespace |
| `ExpressionError: expected X` | Parse error |
| `ExpressionError: unknown function: X` | Function call to non-built-in |
| `ExpressionError: empty variable path` | Bare `.` somewhere |

All errors are logged at the cue host and abort the guard (transition skipped).

## Examples

```yaml
# Guard: only when Pawly has stored history
- when: "peers.Pawly.total_rounds > 0"
  target: processing

# Guard: when recent inbound mentions "project:"
- when: "peers.Pawly.history.last_inbound_contains('project:')"
  target: project_kickoff

# Guard: heavy traffic threshold (cross-peer)
- when: "history.total_rounds() > 100"
  target: high_traffic

# Method + count comparison
- when: "peers.Pawly.history.count() >= 50"
  target: heavy_user

# Mixed: event type AND history state
- when: "event.type == 'A2A_MESSAGE' && peers.Pawly.last_round > 0"
  target: contextual_reply
```
