# AgentWire-Cue v1.4.3 — Retrospective SPEC-PATCH

> **Date**: 2026-06-07 (back-fill for v1.4.3 which shipped 2026-06-06)
> **Tag**: `v1.4.3` (annotated, commit `798455c` → dereference `2c1e083`)
> **Owner**: 丝线 (SilkThread)
> **Companion**: [agentwire-core v1.4.3 SPEC-PATCH](../../agentwire_core/designs/v1.4.3/SPEC-PATCH.md)

---

## 🎯 v1.4.3 in one paragraph

v1.4.3 turns AgentWire-Cue from a statechart engine into a **history-aware orchestrator**. Plugins can now subscribe to per-peer message history changes, query history through first-class expression namespaces (`peers.*` and `history.*`), and consume the same redaction pattern catalog that the gateway uses. The expression grammar is extended to support method-call syntax (`a.b.c(args)`) on object proxies.

## 📦 What's in v1.4.3

### New modules
- **`core/history_client.py`** (75 lines): `HistoryClient` with 30s LRU cache, thread-safe, talks CORE's `messages/*` JSON-RPC
- **`core/history_proxy.py`** (110 lines): proxy classes (`_PeersNamespace` / `_PeerProxy` / `_HistoryNamespace`) that make expressions like `peers.Pawly.history.last_inbound_contains("urgent:")` natural
- **`core/redact.py`** (90 lines): `RedactClient` — startup-time pull of CORE's `/redact/patterns`, 24h local cache, builtin 2-pattern fallback if CORE unreachable

### Modified modules
- **`core/expression.py`**: 3 changes
  - `_ALLOWED_NAMESPACES` adds `peers` and `history`
  - parser accepts `a.b.c(args)` method-call syntax (new AST `method` node)
  - `_resolve_path` uses `getattr` fallback for object proxies (not just dicts)
- **`core/statechart.py`**: `EvalEnv.history_client` field; `as_dict()` injects `peers` and `history` namespaces; `StatechartEngine` constructor accepts `history_client`
- **`core/trigger_impl.py`**: `HistoryChangeTrigger` — polls CORE every Ns, fires on round boundary
- **`core/host.py`**: builds `HistoryClient` at startup, injects into all plugin statecharts + trigger scheduler

### SKILL library
- `skill/SKILL.md` / `skill/SKILL_CN.md`: high-level cue overview
- `skill/PLUGIN_AUTHORING.md`: detailed YAML plugin authoring reference
- `skill/EXPRESSION_REFERENCE.md`: full expression grammar + v1.4.3 namespace additions
- `skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md`: cross-platform deployment guide

## 🔧 Expression API (v1.4.3 namespaces)

### `peers.<name>.*`

| Path / Method | Returns | Type |
|---|---|---|
| `peers.<name>.uuid` | string | dot-path |
| `peers.<name>.name` | string | dot-path |
| `peers.<name>.last_round` | int | dot-path |
| `peers.<name>.total_rounds` | int | dot-path |
| `peers.<name>.last_ts` | string (ISO) | dot-path |
| `peers.<name>.history.last(n=5)` | list[dict] | method |
| `peers.<name>.history.last_n_rounds(n=5)` | list[dict] | method (alias) |
| `peers.<name>.history.count()` | int | method |
| `peers.<name>.history.last_round()` | int | method |
| `peers.<name>.history.last_inbound_contains(needle)` | bool | method |
| `peers.<name>.history.last_outbound_contains(needle)` | bool | method |

### `history.*`

| Method | Returns | Notes |
|---|---|---|
| `history.total_rounds()` | int | sum across all known peers |
| `history.total_rounds_today()` | int | v1.4.3 placeholder: same as `total_rounds()` (per-message date filter deferred) |
| `history.peer_count()` | int | |
| `history.peer_names()` | list[str] | |

## 🔄 `history_change` trigger

```yaml
triggers:
  - name: on_pawly_round
    type: history_change
    peer: "Pawly"          # or "*" for any peer
    granularity: round     # round | message | manual
    poll_interval_seconds: 30
```

| Granularity | Fires when… |
|---|---|
| `round` (default) | A peer's `last_round` increases by 1 (or more) between polls |
| `message` | Any new message detected (finer but noisier) |
| `manual` | Only via explicit operator `cue trigger <plugin> manual` |

Fired event:
```yaml
event:
  type: history_change
  payload:
    peer: Pawly
    prev_round: 4
    new_round: 5
    new_count: 1
    granularity: round
```

## 🔄 Method-call grammar extension

v1.4.3 extends the v1.2 expression grammar to support `a.b.c(args)` method-call syntax.
This is purely additive — all v1.2 expressions still parse identically.

```yaml
# v1.4.3: method calls on object proxies
- when: "peers.Pawly.history.last_inbound_contains('urgent:')"
- when: "peers.Pawly.history.count() > 100"
- when: "history.total_rounds() > 500"
```

Internally this is a new AST node `{"op": "method", "path": [...], "args": [...]}`.

## 🧪 Test coverage

- All 234 v1.4.2 cue tests still pass
- v1.4.3 added 7 new tests for history + history_change (smoke-tested in implementation phase; formally added to v1.4.4 as `test_owner_alert.py`)
- v1.4.4 baseline: 241 tests

## 🛡 Security

- All commit authors use the `silk-thread <silk-thread@agentwire.local>` pseudonym
- CUE never sees raw secrets: redaction happens CORE-side before any history is sent to CUE
- Bearer token for CORE never enters the prompt env (read from `AGENTWIRE_TOKEN` env or `--a2a-token-file`)

## 🔗 Forward-looking

v1.4.4 (next release) is a micro-improvement:
- adds `owner-alert` example (killer scenario for `history_change`)
- adds end-to-end `curl` walkthrough in `PROTOCOL_QUICK_REF.md` (CORE side)
- retroactive `CHANGELOG.md` (this file) and `designs/v1.4.3/SPEC-PATCH.md`

---

*Owner: 丝线 (SilkThread)*
*Companion: [agentwire-core v1.4.3 SPEC-PATCH](../../agentwire_core/designs/v1.4.3/SPEC-PATCH.md)*
