# owner-alert — AgentWire-Cue Killer Example (v1.4.4)

## What is it?

A complete, runnable cue plugin demonstrating **v1.4.3's new `history_change` trigger** and **`peers.X.history.last_inbound_contains()` expression** in a realistic scenario.

When **either** Pawly **or** 初梦 receives a message containing the keyword `urgent:`, this plugin fires `send_a2a` to the main agent (初梦). The main agent is then responsible for forwarding the alert to the human user via its telegram channel (out of v1.4.4 scope — owned by 初梦/OpenClaw).

## Why "killer"?

- **Real problem solved**: 30 lines of YAML replaces hundreds of lines of traditional "cron + regex + IM push" code.
- **Shows the soul of v1.4.3**: "history on-demand lookup + trigger on-demand firing" in one place.
- **Copy-pasteable template**: replace `urgent:` with `error:` / `审批` / `@主人` and you've got a new plugin.
- **Ecosystem bootstrapping**: first "worth-cloning" template for the AGENTWIRE-CUE community.

## How to run

```bash
# 1. Start AgentWire CORE
cd /path/to/agentwire_core/server
python3 start.py --host 127.0.0.1 --port 18800 --token-file /tmp/agentwire.token

# 2. Start cue host with this plugin
cd /path/to/agentwire_cue
python3 -m agentwire_cue host \
  --plugin-dir ../examples \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/agentwire.token \
  --admin-token "admin-secret"

# 3. (Manual test) Send an urgent message to CORE, simulating Pawly:
curl -X POST http://127.0.0.1:18800/a2a/rest/message/send \
  -H "Authorization: Bearer $(cat /tmp/agentwire.token)" \
  -H "Content-Type: application/json" \
  -d '{"contextId":"pawly-test-1","message":{"parts":[{"type":"text","text":"urgent: 视频脚本出错请检查"}]}}'

# 4. Within 15s, cue's history_change trigger should fire,
#    and (via main agent) the user should see a notification.
```

## Test coverage (v1.4.4 scope)

This plugin is unit-tested in `tests/test_owner_alert.py`:

| Test | What it verifies |
|------|------------------|
| `test_yaml_load` | yaml 加载 + schema 校验通过 |
| `test_history_change_trigger_registered` | 2 个 history_change trigger 都被 loader 注册 |
| `test_state_initial` | 初始状态是 `watching` |
| `test_history_change_event_watching_to_notify` | mock `history_change` 事件 + 含 urgent: → 转移到 `notify` |
| `test_history_change_event_no_urgent` | mock `history_change` 事件 + 不含 urgent: → 留在 `watching` |
| `test_send_a2a_dispatched_to_main` | notify 状态触发 send_a2a → peer=main, text 含 "🚨" + round number |
| `test_multi_peer_parallel` | Pawly + 初梦 双 trigger 不冲突 |

## What's OUT of scope (v1.4.4)

- ❌ End-to-end TG notification verification
- ❌ OpenClaw main agent's A2A→TG channel routing
- ❌ Production-grade retry / fallback for `send_a2a`

These are owned by the main agent (初梦) and the user — not by the cue plugin author.

## Plugin schema (v1.4.4 update)

This plugin requires `plugin.schema.json` to recognize the `history_change` trigger type. The v1.4.4 release adds this — v1.4.3 shipped the runtime code but missed the schema bump.

## Versioning

| Version | Date | Notes |
|---------|------|-------|
| 1.4.4.0 | 2026-06-07 | Initial release in v1.4.4 |

## Author

- Spec: 初梦 (Chu Meng) — see `MEMORY-v1.4.4-proposal.md`
- Implementation: 丝线 (SilkThread)
- Review: 初梦 (asynchronous)
