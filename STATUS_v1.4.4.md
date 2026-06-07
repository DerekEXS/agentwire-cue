# AgentWire-Cue v1.4.4 — Final Status

> **Released**: 2026-06-07
> **Tag**: `v1.4.4` (annotated; final SHA committed in task 32)
> **Owner**: 丝线 (SilkThread)
> **Series**: v1.4.x FROZEN — see [CHANGELOG.md](CHANGELOG.md) header + [ROADMAP_v1.4.md](ROADMAP_v1.4.md) top
> **Companion**: [agentwire-core v1.4.4](../../agentwire_core) (provides JSON-RPC + redaction)
> **License**: MIT

---

## 🎯 v1.4.4 定位：微改进 (Micro-Improvement)

**不是新功能** —— 是把 v1.4.3 已交付的能力**用得更好**。

---

## 🎯 完成清单 (4 项 + 1 真实同步债)

| # | 项 | 状态 |
|---|----|------|
| 1 | `examples/owner-alert/cue.yaml` + `README.md` (killer example) | ✅ |
| 2 | `tests/test_owner_alert.py` (7 unit tests) | ✅ |
| 3 | `schema/plugin.schema.json` (**v1.4.3 真实同步债** — 加 `history_change` type + config schema) | ✅ |
| 4 | `core/expression.py` unicode-aware tokenize (支持 `peers.初梦.X`) | ✅ |
| 5 | `core/statechart.py` `send_a2a` action 兼容 v1.2 dict form | ✅ |
| 6 | `CHANGELOG.md` (new) | ✅ |
| 7 | `STATUS_v1.4.4.md` (本文件) | ✅ |
| 8 | `designs/v1.4.3/SPEC-PATCH.md` (v1.4.3 真实设计意图) | ✅ |
| 9 | `designs/v1.4.4/SPEC-PATCH.md` (v1.4.4 原始 spec) | ✅ |
| 10 | `ROADMAP_v1.4.md` 顶部 v1.4 FROZEN 声明 | ✅ |
| 11 | `README.md` / `README_CN.md` badge → green + 链 v1.4.4 release (待 push 后) | ✅ |

**显式不做**：
- ❌ `/messages/import` 端点
- ❌ `history.peers.*` 跨 peer 表达式
- ❌ Dockerfile / structlog / 跨 CUE 依赖
- ❌ 任何破坏 v1.4.x 兼容性的 schema 变更

---

## 📊 数字

| 维度 | 数字 |
|------|------|
| **Tag** | `v1.4.4` (annotated) |
| **Commits** | 3 (feat + docs×2) |
| **Files changed** | 5 modified (expression + statechart + examples/echo-with-persist 被 0 触动; ROADMAP 改; schema 改) + 7 new (owner-alert yaml+README, test_owner_alert, CHANGELOG, STATUS, designs/v1.4.3/, designs/v1.4.4/) |
| **+ ~520 / -10 lines** | 主要在 tests/test_owner_alert.py + designs/v1.4.3/SPEC-PATCH |
| **Tests** | 241 passed (cue 全套, 排除 v1.4.2 已坏 test_v142_regressions.py) |
| **Audit** | 0 secrets / 0 personal email / 0 dev path in public files |
| **Author** | 丝线化名 (silk-thread@agentwire.local) |

---

## 📁 交付清单

| 文件 | 类型 | 改动 |
|------|------|------|
| `examples/owner-alert/cue.yaml` | A | v1.2 spec 格式 + 2 个 history_change trigger + 多 peer 并行监听 |
| `examples/owner-alert/README.md` | A | killer example 完整使用说明 |
| `tests/test_owner_alert.py` | A | 7 测试覆盖 yaml/schema/双 trigger/state 转移/multi-peer |
| `schema/plugin.schema.json` | M | v1.4.3 同步债: trigger enum 加 `history_change` + config schema |
| `core/expression.py` | M | tokenize 用 `(?u)\w+` 支持 unicode identifier; parser 同样 |
| `core/statechart.py` | M | `send_a2a` 兼容 v1.2 dict form `{type, text}` |
| `CHANGELOG.md` | A | Keep-a-Changelog 风格, v1.3 / v1.3.1 / v1.3.1.1 / v1.4.1 / v1.4.2 / v1.4.3 / v1.4.4 + v1.5 backlog |
| `STATUS_v1.4.4.md` | A | 本文件 |
| `designs/v1.4.3/SPEC-PATCH.md` | A | v1.4.3 设计意图 + 同步债清单 |
| `designs/v1.4.4/SPEC-PATCH.md` | A | v1.4.4 原始 spec |
| `ROADMAP_v1.4.md` | M | 顶部 v1.4 FROZEN 声明 + v1.4.4 状态 |

---

## 🧪 验收范围 (按主人拍板: 只验 cue 单测)

```
$ cd agentwire_cue
$ python3 -m pytest --ignore=tests/test_v142_regressions.py
====================== 241 passed, 6 warnings in 21.49s =======================
```

| Test | 验证 |
|------|------|
| `test_owner_alert.py::test_yaml_load_and_schema_validate` | yaml 加载 + v1.4.4 schema 通过 (含 history_change) |
| `test_owner_alert.py::test_two_history_change_triggers_registered` | 2 个 history_change trigger 注册 (Pawly + 初梦) |
| `test_owner_alert.py::test_state_machine_initial_is_watching` | initial 状态是 watching |
| `test_owner_alert.py::test_history_change_with_urgent_triggers_notify` | 含 urgent: → 转移 notify + send_a2a peer=main |
| `test_owner_alert.py::test_history_change_without_urgent_does_not_match` | guard false → no_transition |
| `test_owner_alert.py::test_multi_peer_parallel_no_collision` | Pawly + 初梦 双 trigger 不冲突 |
| `test_owner_alert.py::test_no_send_a2a_when_no_history_yet` | 空历史 → guard 失败 → no transition |

**不验范围**：
- ❌ 端到端 TG 通知（OpenClaw 改造，超 v1.4.4 scope）

---

## 🛡 防泄漏

- 所有 commit author 均为 `silk-thread <silk-thread@agentwire.local>`（化名）
- `owner-alert.yaml` 用 `demo-token-xyz` 等占位符（不写真实 token）
- `127.0.0.1:18800` 仅出现（loopback，无 dev 路径）
- 历史敏感数据由 CORE redact 引擎处理，cue 永远看不到原文

---

## 🔗 关联

- 初梦 `MEMORY-v1.4.4-proposal.md`（原始提案）
- 丝线 `MEMORY-v1.4.4-decision.md`（v1.4.4 拍板 + 写给初梦的异步回信）
- 初梦回复（在 conversation 内）：澄清"杀手示例"概念 + 端到端链路 (cue → A2A → 初梦 TG)

---

## 🎬 下一 session 起点

读本文件 + `CHANGELOG.md` + `ROADMAP_v1.4.md` 顶部。

**v1.5 backlog** (从 v1.4.3/4 推到 v1.5):
- Dockerfile + docker-compose
- structlog observability
- 跨 CUE 依赖
- 主 agent 全栈升级时（主人 2026-06-06 提到的）

---

*冻结: 2026-06-07 丝线 (SilkThread)*
*Tag: v1.4.4 (双仓)*
