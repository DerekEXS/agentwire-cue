# AgentWire v1.4.4 — SPEC-PATCH

> **范围**: v1.4.4 微改进 (Micro-Improvement)
> **基础**: v1.4.3 (tag `v1.4.3`, commit `cf6d5ed` core / `2c1e083` cue)
> **起草人**: 丝线 (SilkThread)
> **关联**: 初梦 `MEMORY-v1.4.4-proposal.md`、丝线 `MEMORY-v1.4.4-decision.md`

---

## 🎯 定位

v1.4.4 是 **微改进 release**，不是新功能 release。在 v1.4.3 已交付的 `history` / `redact` / `peers.*` 表达式 / `history_change` trigger 基础上，**把这些能力"用得更好"**：

- 把 `history_change` trigger 用一个**真实场景**的 killer example 立起来
- 把协议层的 `messages/*` JSON-RPC 用**端到端 curl walkthrough** 暴露给人类用户
- 把 v1.4.3 当初**没补的 spec 同步债** 收口
- 把 v1.4 大版本**显式 FROZEN**，给 v1.5 划清边界

**绝对不做**:
- 新增 endpoint（除文档增补）
- 新增 trigger 类型
- 新增 expression 命名空间
- 任何破坏 v1.4.x 兼容性的 schema 变更

---

## 📦 4 项实施内容

### 项 1: Killer Example — `owner-alert`

**新增文件**:
- `agentwire_cue/examples/owner-alert/cue.yaml`
- `agentwire_cue/examples/owner-alert/README.md`

**核心概念**:
- Pawly 收到主人发的 `urgent:` 关键词消息
- cue 用 `history_change` trigger 检测 round 变化
- cue 用 `peers.Pawly.history.last_inbound_contains('urgent:')` 表达式判定
- cue `send_a2a` 给 main agent (初梦)
- 初梦收 A2A 后经 telegram channel 通知主人（**此段归初梦侧，v1.4.4 不验**）

**cue 侧 yaml 草稿**:
```yaml
id: owner-alert
version: 1.0.4.0
description: Pawly/初梦 任一 peer 收到含 urgent: 关键词 → 通知主 agent

triggers:
  - name: on_pawly_urgent
    type: history_change
    peer: "Pawly"
    granularity: round
    poll_interval_seconds: 15

  - name: on_chumeng_urgent
    type: history_change
    peer: "初梦"
    granularity: round
    poll_interval_seconds: 15

statechart:
  initial: watching

  states:
    watching:
      transitions:
        - when: "peers.Pawly.history.last_inbound_contains('urgent:') || peers.初梦.history.last_inbound_contains('urgent:')"
          target: notify

    notify:
      on_enter:
        - action: send_a2a
          params:
            peer: "main"
            text: "🚨 {{ event.peer }} 紧急消息: round {{ event.new_round }} — {{ peers.Pawly.history.last_inbound_contains('urgent:') && 'from Pawly' || 'from 初梦' }}"
      transitions:
        - { target: watching }
```

**cue 单测（v1.4.4 验收范围）**:
1. yaml schema 校验通过
2. expression 解析无错（v1.4.3 method-call 语法支持）
3. 模拟 `messages/peers` 返回 `[{name: Pawly, last_round: N+1}]` → 触发 `on_pawly_urgent` → state machine 进入 `notify` → `send_a2a` 调度
4. 模拟 "Pawly 上一条 inbound 不含 urgent:" → state 留在 `watching`
5. 模拟 "Pawly 上一条 inbound 含 urgent:" → state 进入 `notify` → 不重复触发（因为 transition 回到 `watching`）

---

### 项 2: `PROTOCOL_QUICK_REF.md` 增补端到端 curl

**修改文件**: `agentwire_core/skill/PROTOCOL_QUICK_REF.md`

**追加章节**: "End-to-end curl walkthrough"

**结构** (主人 0 配置可跑):
1. Setup: 写 token → 启动 CORE (单条命令)
2. Step 1: 查 Agent Card (`GET /.well-known/agent.json`)
3. Step 2: 发消息 (`POST /a2a/rest/message/send`)
4. Step 3: 查 history (模拟初梦 session 收到回复后)
5. Step 4: 列所有 peer (`POST /a2a/jsonrpc messages/peers`)
6. Step 5: 拉某 peer 最近 N 轮 (`POST /a2a/jsonrpc messages/list`)

**目的**: 主人能复制粘贴直接验证整个 A2A 流，**不依赖任何 AGENT 中转**。

---

### 项 3: v1.4.3 同步债

**新文件**:
- `agentwire_core/designs/v1.4.3/SPEC-PATCH.md` — 把 v1.4.3 的 history / redact / SKILL 库设计意图定格
- `agentwire_cue/designs/v1.4.3/SPEC-PATCH.md` — 同上，cue 侧

**新建文件**:
- `agentwire_core/CHANGELOG.md` — 包含 v1.3.x / v1.4.1 / v1.4.2 / v1.4.3 / v1.4.4 entries
- `agentwire_cue/CHANGELOG.md` — 同上

**CHANGELOG.md 格式** (Keep a Changelog 风格):
```markdown
# Changelog
All notable changes to this project will be documented in this file.

## [v1.4.4] - 2026-06-07
### Added
- examples/owner-alert: killer example for history_change trigger (cue)
- skill/PROTOCOL_QUICK_REF.md: end-to-end curl walkthrough (core)

## [v1.4.3] - 2026-06-06
### Added
- Per-peer JSONL history persistence with auto-redaction (core)
- ...
```

---

### 项 4: v1.4 spec FROZEN

**修改文件**: `agentwire_cue/ROADMAP_v1.4.md`

**变更**: 顶部"v1.4 整体状态"段补"FROZEN"字样和"v1.4 大版本收尾"声明

**新建**: `agentwire_core/CHANGELOG.md` 顶部加 `## v1.4.x 系列 (2026-06 FROZEN)` 段落

**含义**: v1.4 大版本正式收尾。新工作归 v1.5 (Dockerfile / structlog / cross-cue / `messages/import`)。

---

## 🧪 验收清单 (v1.4.4)

### cue 单测 (丝线自验)

| 测试 | 描述 |
|------|------|
| `test_owner_alert_yaml_load` | yaml 加载 + schema 校验通过 |
| `test_owner_alert_expr_parse` | `peers.X.history.last_inbound_contains(...)` 解析无错 |
| `test_owner_alert_trigger_fire` | mock `messages/peers` 返回 `last_round += 1` → trigger 触发 |
| `test_owner_alert_state_watching_to_notify` | 含 urgent: → 转移到 notify 状态 |
| `test_owner_alert_state_notify_to_watching` | transition 回到 watching，不重复触发 |
| `test_owner_alert_multi_peer` | Pawly + 初梦 双 peer 并行监听不冲突 |

### core 端点单测 (丝线自验)

| 测试 | 描述 |
|------|------|
| `test_protocol_quick_ref_walkthrough` | 文档里 6 步串联 curl 命令全跑通（不验证主人手敲，但脚本要能跑） |
| `test_changelog_md_format` | CHANGELOG.md 符合 Keep a Changelog 风格 |
| `test_designs_v143_spec_patch_exists` | 双仓 designs/v1.4.3/SPEC-PATCH.md 存在且 ≥ 50 行 |

### 不验的（v1.4.4 scope 外）

- ❌ owner-alert 端到端 TG 通知（初梦 OpenClaw 改造）
- ❌ cue host 启动后真实 A2A 通讯（mock 已覆盖单测）
- ❌ CHANGELOG.md 历史补录到 v1.3.x（保留 "TBD" 占位即可）

---

## 🏷 Tag 计划

- `v1.4.4` annotated tag in 双仓
- core: 指向 commit `cf6d5ed` 之后的所有 v1.4.4 commits
- cue: 指向 commit `2c1e083` 之后的所有 v1.4.4 commits
- GitHub Release v1.4.4 (Composio)

---

## 📅 实施顺序

1. 起草 SPEC-PATCH（已写）
2. cue 仓: commit 1 (feat: examples/owner-alert + cue tests)
3. core 仓: commit 1 (docs: PROTOCOL_QUICK_REF 增补)
4. core 仓: commit 2 (docs: CHANGELOG.md + designs/v1.4.3/SPEC-PATCH.md)
5. cue 仓: commit 2 (docs: CHANGELOG.md + designs/v1.4.3/SPEC-PATCH.md + ROADMAP FROZEN 声明)
6. 双仓写 STATUS_v1.4.4.md + commit 3 (docs)
7. 修正 README status badge → green (双仓 × EN/CN = 4 文件)
8. git tag v1.4.4 (双仓) + force-push (如前 v1.4.3 模式)
9. GitHub Release v1.4.4 (Composio)
10. 报告主人

---

*维护者: 丝线 (SilkThread)*
*预计完成: 2026-06-07 6-8 小时内*
*下游: 主人验收 cue 单测 + 初梦异步 review + 主人全栈升级时端到端验*
